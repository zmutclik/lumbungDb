#!/bin/bash
# Tulis konfigurasi tuning MariaDB sesuai resource container. Dijalankan entrypoint setiap container start,
# jadi kalau limit container diubah, config ikut menyesuaikan setelah restart.
#
# DB_TUNING          : auto (default) | off
# DB_MEMORY          : jatah memori untuk MariaDB, mis. 4G / 4096M. Kosong = dari limit container
# DB_MAX_CONNECTIONS : default 200
# DB_DISK            : ssd (default) | hdd
#
# Nilai yang ingin dipaksa manual: mount file .cnf yang namanya urut setelah zz-tuning.cnf (mis. zzz-custom.cnf).

TUNING_CNF=/etc/mysql/conf.d/zz-tuning.cnf

if [ "${DB_TUNING:-auto}" = "off" ]; then
    rm -f "$TUNING_CNF"
    echo "Tuning otomatis nonaktif (DB_TUNING=off)"
    exit 0
fi

# Ukuran dalam MB. Angka tanpa satuan dianggap MB
to_mb() {
    local v="${1^^}"
    [[ "$v" =~ ^([0-9]+)([GM]?)$ ]] || return 1
    if [ "${BASH_REMATCH[2]}" = "G" ]; then
        echo $(( BASH_REMATCH[1] * 1024 ))
    else
        echo "${BASH_REMATCH[1]}"
    fi
}

clamp() {
    local v=$1 min=$2 max=$3
    (( v < min )) && v=$min
    (( v > max )) && v=$max
    echo "$v"
}

host_mb=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)

# Limit memori container: cgroup v2, lalu v1. "Tanpa limit" di v1 berupa angka sangat besar, jadi dibandingkan dengan RAM host
limit_mb=""
if [ -r /sys/fs/cgroup/memory.max ]; then
    v=$(cat /sys/fs/cgroup/memory.max)
    [ "$v" != "max" ] && limit_mb=$(( v / 1024 / 1024 ))
elif [ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
    limit_mb=$(( $(cat /sys/fs/cgroup/memory/memory.limit_in_bytes) / 1024 / 1024 ))
fi
if [ -n "$limit_mb" ] && (( limit_mb >= host_mb )); then
    limit_mb=""
fi

if [ -n "$DB_MEMORY" ]; then
    if ! mem_mb=$(to_mb "$DB_MEMORY") || (( mem_mb < 512 )); then
        echo "DB_MEMORY tidak valid: '$DB_MEMORY' (contoh: 4G atau 4096M, minimal 512M), tuning dilewati" >&2
        rm -f "$TUNING_CNF"
        exit 0
    fi
    mem_source="DB_MEMORY"
    if [ -n "$limit_mb" ] && (( mem_mb > limit_mb )); then
        echo "DB_MEMORY ($DB_MEMORY) melebihi limit container (${limit_mb}M), dipakai limit container" >&2
        mem_mb=$limit_mb
        mem_source="limit container"
    fi
elif [ -n "$limit_mb" ]; then
    mem_mb=$limit_mb
    mem_source="limit container"
else
    # Tanpa limit, RAM yang terbaca adalah RAM seluruh host yang mungkin dipakai container lain
    mem_mb=$(( host_mb / 2 ))
    mem_source="50% RAM host"
    echo "PERINGATAN: container tanpa limit memori dan DB_MEMORY kosong, dipakai 50% RAM host (${mem_mb}M)" >&2
fi

# CPU: cgroup v2 "quota period" / v1 cfs_quota, dibulatkan ke atas. Tanpa limit = jumlah CPU yang terlihat
cpus=$(nproc)
if [ -r /sys/fs/cgroup/cpu.max ]; then
    read -r quota period </sys/fs/cgroup/cpu.max
elif [ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then
    quota=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)
    period=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)
fi
if [[ "$quota" =~ ^[0-9]+$ ]] && (( quota > 0 )); then
    limit_cpus=$(( (quota + period - 1) / period ))
    (( limit_cpus < cpus )) && cpus=$limit_cpus
fi

max_conn="${DB_MAX_CONNECTIONS:-200}"
if ! [[ "$max_conn" =~ ^[0-9]+$ ]] || (( max_conn < 10 )); then
    echo "DB_MAX_CONNECTIONS tidak valid: '$DB_MAX_CONNECTIONS', dipakai 200" >&2
    max_conn=200
fi

# Buffer pool: porsi terbesar. Sisanya untuk overhead server, tabel sementara, dan buffer per koneksi
# (sort/join/read buffer + stack thread, dihitung ±2MB per koneksi)
if (( mem_mb < 2048 )); then
    pool_pct=50
elif (( mem_mb < 8192 )); then
    pool_pct=65
else
    pool_pct=75
fi
reserve_mb=$(clamp $(( mem_mb / 10 )) 256 2048)
pool_mb=$(( mem_mb * pool_pct / 100 ))
pool_max_mb=$(( mem_mb - reserve_mb - max_conn * 2 ))
(( pool_mb > pool_max_mb )) && pool_mb=$pool_max_mb
# Kelipatan 128M (chunk buffer pool), minimal 128M
pool_mb=$(( pool_mb / 128 * 128 ))
(( pool_mb < 128 )) && pool_mb=128

# Redo log ±25% buffer pool: cukup besar agar checkpoint tidak terlalu sering, tapi recovery setelah crash tetap cepat
log_mb=$(clamp $(( pool_mb / 4 )) 96 4096)
(( pool_mb >= 4096 )) && log_buffer_mb=64 || log_buffer_mb=16

tmp_mb=$(clamp $(( mem_mb / 64 )) 16 256)
table_cache=$(clamp $(( max_conn * 10 )) 2000 10000)
io_threads=$(clamp "$cpus" 4 16)
thread_cache=$(clamp $(( max_conn / 4 )) 16 256)

case "${DB_DISK:-ssd}" in
    hdd) io_capacity=200;  io_capacity_max=400;  flush_neighbors=1 ;;
    ssd) io_capacity=2000; io_capacity_max=4000; flush_neighbors=0 ;;
    *)
        echo "DB_DISK tidak valid: '$DB_DISK' (ssd/hdd), dipakai ssd" >&2
        io_capacity=2000; io_capacity_max=4000; flush_neighbors=0
        ;;
esac

if ! cat >"$TUNING_CNF" <<EOF
# Dibuat otomatis oleh /opt/tuning.sh saat container start, jangan diedit (akan ditimpa)
# Memori: ${mem_mb}M (${mem_source}), CPU: ${cpus}, disk: ${DB_DISK:-ssd}
[mysqld]
max_connections = ${max_conn}
thread_cache_size = ${thread_cache}

innodb_buffer_pool_size = ${pool_mb}M
innodb_log_file_size = ${log_mb}M
innodb_log_buffer_size = ${log_buffer_mb}M
innodb_read_io_threads = ${io_threads}
innodb_write_io_threads = ${io_threads}
innodb_io_capacity = ${io_capacity}
innodb_io_capacity_max = ${io_capacity_max}
innodb_flush_neighbors = ${flush_neighbors}

tmp_table_size = ${tmp_mb}M
max_heap_table_size = ${tmp_mb}M
table_open_cache = ${table_cache}
table_definition_cache = ${table_cache}
EOF
then
    echo "Gagal menulis $TUNING_CNF, MariaDB jalan dengan config default" >&2
    exit 0
fi
chmod 644 "$TUNING_CNF"

echo "Tuning MariaDB: memori ${mem_mb}M (${mem_source}), CPU ${cpus}, disk ${DB_DISK:-ssd}"
echo "  buffer pool ${pool_mb}M, redo log ${log_mb}M, max_connections ${max_conn}, tmp table ${tmp_mb}M"
