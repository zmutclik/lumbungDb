#!/bin/sh
# LumbungDB: memulihkan seluruh satu container MariaDB dari backup di server ini. Script ini tidak berisi rahasia.
#   curl -fsSL <url>/restore.sh | LUMBUNG_URL=<url> RESTORE_TOKEN=lmr_xxx AGE_IDENTITY_FILE=/path/kunci.key sh
#
# AGE_IDENTITY_FILE adalah kunci pemulihan (age) yang disimpan offline; tanpa kunci ini file backup tidak bisa dibuka.
# Pilihan lewat variabel:
#   RESTORE_DIR=/path   folder kerja dan data, bawaan <folder tempat perintah dijalankan>/lumbung-restore-<nama>
#   DB_MEM_LIMIT=4g DB_CPUS=4   batas container hasil (bawaan 2g dan 2)
#
# Aman dijalankan ulang di mesin yang sama sebelum batas waktu sesi: unduhan dilanjutkan, dan bila database sudah hidup
# tetapi pendaftaran gagal, hanya pendaftaran yang diulang.
set -eu
umask 077

SESSION_OK=0
die() {
    printf 'GAGAL: %s\n' "$*" >&2
    [ "$SESSION_OK" = "1" ] && report fail "$*" || true
    exit 1
}
step() { printf '==> %s\n' "$*"; [ "$SESSION_OK" = "1" ] && report ok "$*" || true; }
random_text() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32; }
env_value() { sed -n "s/^$1=//p" "$2" | head -n 1; }

port_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q "[:.]$1\$"
    else
        return 1
    fi
}

# Token lewat file konfigurasi curl, bukan argumen, supaya tidak terlihat di daftar proses.
api() {  # api <metode> <path> [curl-arg...]; jawaban ke $WORK/reply, kode HTTP ke stdout
    method=$1; path=$2; shift 2
    curl -sS --max-time 120 -o "$WORK/reply" -w '%{http_code}' -K "$WORK/curl.cfg" -X "$method" "$@" "$BASE_URL$path"
}

report() {  # report ok|fail <teks>: kegagalan melapor tidak boleh menghentikan script
    printf '%s %s' "$1" "$2" | curl -sS --max-time 20 -o /dev/null -K "$WORK/curl.cfg" -X POST \
        -H 'Content-Type: text/plain' --data-binary @- "$BASE_URL/api/manual/progress" >/dev/null 2>&1 || true
}

check_server() {
    step "Memeriksa server"
    command -v curl >/dev/null 2>&1 || die "curl tidak ada."
    command -v docker >/dev/null 2>&1 || die "Docker belum terpasang."
    docker info >/dev/null 2>&1 || die "Tidak bisa memakai Docker. Jalankan sebagai root atau user di grup docker."
    command -v sha256sum >/dev/null 2>&1 || die "sha256sum tidak ada."
    [ -s /etc/machine-id ] || die "/etc/machine-id tidak ada; LumbungDB memakainya untuk mengikat sesi ke server ini."
    [ -s /etc/hostname ] || die "/etc/hostname tidak ada."
    [ -r "$AGE_FILE" ] || die "AGE_IDENTITY_FILE tidak terbaca: $AGE_FILE"
}

request_session() {
    machine_id=$(head -n 1 /etc/machine-id)
    {
        printf 'header = "X-Restore-Token: %s"\n' "$TOKEN"
        printf 'header = "X-Machine-Id: %s"\n' "$machine_id"
    } > "$WORK/curl.cfg"
    printf '==> Mengambil sesi dari %s\n' "$BASE_URL"
    status=$(api POST /api/manual/session) || die "Tidak bisa menghubungi $BASE_URL."
    [ "$status" = "200" ] || { printf 'GAGAL: %s (HTTP %s)\n' "$(head -c 500 "$WORK/reply")" "$status" >&2; exit 1; }
    cp "$WORK/reply" "$WORK/session"
    NAME=$(env_value NAME "$WORK/session"); PORT=$(env_value PORT "$WORK/session")
    TAG=$(env_value IMAGE_TAG "$WORK/session"); BINLOG_START=$(env_value BINLOG_START "$WORK/session")
    SOURCE=$(env_value SOURCE "$WORK/session")
    case "$NAME" in ''|*[!A-Za-z0-9_.-]*) die "Jawaban LumbungDB tidak dikenali." ;; esac
    case "$PORT" in ''|*[!0-9]*) die "Jawaban LumbungDB tidak dikenali." ;; esac
    case "$TAG" in ''|*[!A-Za-z0-9.-]*) die "Jawaban LumbungDB tidak dikenali." ;; esac
    IMAGE="zmutclik/mariadb:lumbungdb-$TAG"
    SESSION_OK=1
}

choose_dir() {
    DIR=${RESTORE_DIR:-$(pwd)/lumbung-restore-$NAME}
    mkdir -p "$DIR/dl" || die "Tidak bisa membuat $DIR."
    DIR=$(cd "$DIR" && pwd)
}

# ---------- pemulihan ----------
tool() {  # satu perintah bash di dalam image, tanpa jaringan; /work = folder kerja
    docker run --rm --network none --label lumbung.restore.tmp=1 --entrypoint bash -v "$DIR:/work" \
        -v "$AGE_FILE:/keys/age.key:ro" "$IMAGE" -c "$1"
}

SOCK=/run/mysqld/mysqld.sock
cleanup_tmp() {
    ids=$(docker ps -aq --filter label=lumbung.restore.tmp=1 --filter "name=lumbung-restore-$NAME-" 2>/dev/null || true)
    [ -z "$ids" ] || docker rm -f -v $ids >/dev/null 2>&1 || true
}

wait_sql() {  # wait_sql <container> [password]: server menjawab di socket dengan akun root tanpa tabel hak (tmp) atau runner
    n=0
    while [ "$n" -lt 180 ]; do
        if [ -n "${2:-}" ]; then
            docker exec -e "MYSQL_PWD=$2" "$1" mariadb -S "$SOCK" -ulumbung_runner -BN -e 'SELECT 1' >/dev/null 2>&1 && return 0
        else
            docker exec "$1" mariadb -S "$SOCK" -uroot -BN -e 'SELECT 1' >/dev/null 2>&1 && return 0
        fi
        sleep 1; n=$((n + 1))
    done
    docker logs --tail 15 "$1" >&2 2>&1 || true
    die "MariaDB di $1 tidak naik."
}

offline_container() {  # offline_container <suffix> <opsi mariadbd>: server sementara di datadir hasil restore
    docker run -d --name "lumbung-restore-$NAME-$1" --label lumbung.restore.tmp=1 --network none --entrypoint mariadbd \
        -v "$DIR/data:/var/lib/mysql" "$IMAGE" --user=mysql "--socket=$SOCK" --event-scheduler=OFF --log-bin=OFF \
        --skip-networking $2 >/dev/null || die "Tidak bisa menjalankan MariaDB sementara."
}

download_files() {
    grep '^FILE ' "$WORK/session" | while read -r _ id kind size sha no; do
        dest="$DIR/dl/$id"
        tries=0
        while :; do
            have=0; [ -f "$dest" ] && have=$(wc -c < "$dest")
            [ "$have" -gt "$size" ] && { rm -f "$dest"; have=0; }
            if [ "$have" -lt "$size" ]; then
                step "Mengunduh file $id ($kind, $((have / 1048576)) dari $((size / 1048576)) MB)"
                # -C - melanjutkan dari ukuran file yang sudah ada (Range: bytes=N-)
                curl -fsS --max-time 7200 -K "$WORK/curl.cfg" -C - -o "$dest" "$BASE_URL/api/manual/files/$id" \
                    || { tries=$((tries + 1)); [ "$tries" -ge 5 ] && die "Unduhan file $id gagal berulang kali."; sleep $((tries * 5)); continue; }
            fi
            [ "$(wc -c < "$dest")" = "$size" ] && break
            tries=$((tries + 1))
            [ "$tries" -ge 5 ] && die "Ukuran file $id tidak cocok setelah beberapa kali unduh."
        done
        got=$(sha256sum "$dest" | cut -d' ' -f1)
        [ "$got" = "$sha" ] || { rm -f "$dest"; die "Checksum file $id berbeda: file rusak. Jalankan ulang untuk mengunduh lagi."; }
    done
}

restore_full() {
    full=$(grep '^FILE ' "$WORK/session" | awk '$3 == "fullbackup" {print $2; exit}')
    [ -n "$full" ] || die "Sesi tidak berisi full."
    step "Membuka enkripsi, mengekstrak, dan prepare full"
    tool 'rm -rf /work/data /work/binlog /work/apply.sql' || die "Folder kerja lama tidak bisa dibersihkan."
    mkdir -p "$DIR/data"
    tool "set -eo pipefail; age -d -i /keys/age.key /work/dl/$full | zcat > /work/full.xbstream
          mbstream -x -C /work/data < /work/full.xbstream
          mariadb-backup --prepare --target-dir=/work/data > /work/prepare.log 2>&1 || { tail -5 /work/prepare.log; exit 2; }
          chown -R mysql:mysql /work/data; rm -f /work/full.xbstream" || die "Pemulihan full gagal (buka enkripsi atau prepare)."
    POSITION=$(tool 'cat /work/data/mariadb_backup_binlog_info' 2>/dev/null | awk '{print $2}')
}

apply_binlogs() {
    count=$(grep -c '^FILE [0-9]* binlog ' "$WORK/session" || true)
    [ "$count" -gt 0 ] || { step "Tanpa binlog: data sampai full"; return 0; }
    case "$POSITION" in ''|*[!0-9]*) die "Posisi awal binlog tidak terbaca dari full." ;; esac
    step "Menerapkan $count binlog sampai data terbaru"
    mkdir -p "$DIR/binlog"
    files=""
    for line in $(grep '^FILE [0-9]* binlog ' "$WORK/session" | awk '{print $2 ":" $6}'); do
        id=${line%%:*}; no=${line##*:}
        name=$(printf 'mysql-bin.%06d' "$no")
        tool "set -eo pipefail; age -d -i /keys/age.key /work/dl/$id | zcat > /work/binlog/$name" || die "Binlog $no tidak bisa dibuka."
        files="$files /work/binlog/$name"
    done
    tool "set -eo pipefail; mariadb-binlog --start-position=$POSITION $files > /work/apply.sql" || die "Binlog tidak bisa dibaca."
    docker exec -i -e "MYSQL_PWD=$RUNNER_PW" "lumbung-restore-$NAME-uji" mariadb -S "$SOCK" -ulumbung_runner < "$DIR/apply.sql" \
        || die "Penerapan binlog gagal; database tidak dipakai."
}

verify_tables() {
    tables=$(docker exec -e "MYSQL_PWD=$RUNNER_PW" "lumbung-restore-$NAME-uji" mariadb -S "$SOCK" -ulumbung_runner -BN -e \
        "SELECT table_schema, table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema NOT IN ('information_schema','performance_schema','mysql','sys') ORDER BY data_length DESC LIMIT 15") \
        || die "Daftar tabel tidak terbaca."
    [ -n "$tables" ] || die "Hasil restore tidak berisi tabel."
    printf '%s\n' "$tables" | while read -r schema table; do
        docker exec -e "MYSQL_PWD=$RUNNER_PW" "lumbung-restore-$NAME-uji" mariadb -S "$SOCK" -ulumbung_runner -BN -e \
            "SELECT COUNT(*) FROM \`$schema\`.\`$table\`" >/dev/null || die "Tabel $schema.$table tidak terbaca."
    done
}

prepare_data() {
    restore_full
    RUNNER_PW=$(random_text)
    step "Membuat akun sementara"
    offline_container akun "--skip-grant-tables"
    wait_sql "lumbung-restore-$NAME-akun"
    docker exec "lumbung-restore-$NAME-akun" mariadb -S "$SOCK" -uroot -BN -e \
        "FLUSH PRIVILEGES; CREATE USER 'lumbung_runner'@'localhost' IDENTIFIED BY '$RUNNER_PW'; GRANT ALL PRIVILEGES ON *.* TO 'lumbung_runner'@'localhost' WITH GRANT OPTION;" \
        || die "Akun sementara tidak bisa dibuat."
    docker stop -t 120 "lumbung-restore-$NAME-akun" >/dev/null && docker rm -f -v "lumbung-restore-$NAME-akun" >/dev/null
    step "Memeriksa hasil restore (read-only)"
    offline_container uji "--read-only"
    wait_sql "lumbung-restore-$NAME-uji" "$RUNNER_PW"
    apply_binlogs
    verify_tables
    docker stop -t 120 "lumbung-restore-$NAME-uji" >/dev/null && docker rm -f -v "lumbung-restore-$NAME-uji" >/dev/null
}

make_backup_user() {
    step "Membuat user backup baru dan menghapus akun sementara"
    BACKUP_PASS=$(random_text)
    offline_container akhir "--skip-grant-tables"
    wait_sql "lumbung-restore-$NAME-akhir"
    docker exec "lumbung-restore-$NAME-akhir" mariadb -S "$SOCK" -uroot -BN -e \
        "FLUSH PRIVILEGES; CREATE OR REPLACE USER 'user_backup'@'localhost' IDENTIFIED BY '$BACKUP_PASS';
         GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER, RELOAD, PROCESS, BINLOG MONITOR ON *.* TO 'user_backup'@'localhost';
         CREATE USER IF NOT EXISTS 'lumbung'@'localhost' IDENTIFIED VIA unix_socket;
         GRANT SHOW VIEW ON *.* TO 'lumbung'@'localhost';
         DROP USER IF EXISTS 'lumbung_runner'@'localhost'; FLUSH PRIVILEGES;" \
        || die "User backup tidak bisa dibuat."
    docker stop -t 120 "lumbung-restore-$NAME-akhir" >/dev/null && docker rm -f -v "lumbung-restore-$NAME-akhir" >/dev/null
}

write_env() {  # write_env [dengan-agen]
    {
        if [ "${1:-}" = "agen" ]; then
            printf 'LUMBUNG_URL=%s\nLUMBUNG_TOKEN=%s\nLUMBUNG_AGE_RECIPIENTS=%s\n' "$BASE_URL" "$AGENT_TOKEN" "$AGE_RECIPIENTS"
        fi
        printf 'BACKUP_USER=user_backup\nBACKUP_PASS=%s\nBACKUP_CRON=\nDUMP_CRON=\nBINLOG_CRON=\nBINLOG_UNSENT_MAX_DAYS=3\nMARIADB_AUTO_UPGRADE=1\n' "$BACKUP_PASS"
    } > "$DIR/.env.tmp"
    chmod 600 "$DIR/.env.tmp"
    mv "$DIR/.env.tmp" "$DIR/.env"
}

start_production() {
    docker stop -t 120 "$NAME" >/dev/null 2>&1 || true
    docker rm -f -v "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" --hostname "$NAME" --restart always --env-file "$DIR/.env" -p "$PORT:3306" \
        --memory "${DB_MEM_LIMIT:-2g}" --cpus "${DB_CPUS:-2}" -v "$DIR/data:/var/lib/mysql" -v "$NAME-backup:/backup" \
        -v /etc/hostname:/lumbung/server/hostname:ro -v /etc/machine-id:/lumbung/server/machine-id:ro \
        "$IMAGE" --event-scheduler=ON >/dev/null || die "Tidak bisa menjalankan container $NAME."
    n=0
    while [ "$n" -lt 300 ]; do
        state=$(docker exec -e "MYSQL_PWD=$BACKUP_PASS" "$NAME" mariadb -S "$SOCK" -uuser_backup -BN -e \
            "SELECT @@global.skip_networking, @@global.read_only, @@global.event_scheduler, (SELECT COUNT(*) FROM mysql.global_priv WHERE user='lumbung_runner')" 2>/dev/null || true)
        if [ -n "$state" ]; then
            set -- $state
            if [ "$1" = "0" ]; then
                [ "$2" = "0" ] || die "$NAME masih read_only."
                [ "$3" = "ON" ] || die "Event scheduler $3, seharusnya ON."
                [ "$4" = "0" ] || die "Akun lumbung_runner masih ada."
                return 0
            fi
        fi
        sleep 1; n=$((n + 1))
    done
    docker logs --tail 20 "$NAME" >&2 2>&1 || true
    die "$NAME belum siap setelah 300 detik. Log lengkap: docker logs $NAME"
}

register_agent() {
    step "Mendaftarkan $NAME ke LumbungDB"
    status=$(api POST /api/manual/register) || die "Tidak bisa menghubungi $BASE_URL untuk mendaftar."
    [ "$status" = "200" ] || die "Pendaftaran ditolak: $(head -c 500 "$WORK/reply") (HTTP $status). Database sudah hidup; jalankan perintah yang sama lagi setelah masalahnya diperbaiki."
    AGENT_TOKEN=$(env_value LUMBUNG_TOKEN "$WORK/reply")
    AGE_RECIPIENTS=$(env_value LUMBUNG_AGE_RECIPIENTS "$WORK/reply")
    case "$AGENT_TOKEN" in ''|*[!A-Za-z0-9_-]*) die "Token agen dari LumbungDB tidak dikenali." ;; esac
    write_env agen
    start_production
    printf 'terdaftar\n' > "$DIR/state"
}

cleanup_files() {
    tool 'rm -rf /work/dl /work/binlog /work/apply.sql /work/prepare.log /work/full.xbstream' >/dev/null 2>&1 || true
}

report_done() {
    cat <<EOT

$NAME berjalan.
  Folder data : $DIR/data
  Port        : $PORT (terbuka di semua alamat server ini; batasi di firewall hanya untuk IP aplikasi)
  Akun        : akun database sama dengan container sumber ($SOURCE); user backup baru ada di $DIR/.env
  Batas       : $DB_MEM_LIMIT_SHOWN, ubah dengan menjalankan ulang container dari $DIR/.env

Agen mengirim heartbeat dalam 60 detik dan full pertama langsung dijalankan.
Container sumber ($SOURCE) tidak disentuh: pastikan ia tidak lagi menerima tulisan, lalu arahkan aplikasi ke server ini.
Pantau di $BASE_URL/containers/$NAME
EOT
}

main() {
    BASE_URL=${LUMBUNG_URL:-}; BASE_URL=${BASE_URL%/}
    TOKEN=${RESTORE_TOKEN:-}
    AGE_FILE=${AGE_IDENTITY_FILE:-}
    unset LUMBUNG_URL RESTORE_TOKEN AGE_IDENTITY_FILE 2>/dev/null || true
    case "$BASE_URL" in http://*|https://*) ;; *) die "Isi LUMBUNG_URL, mis. https://lumbung.contoh.id" ;; esac
    case "$TOKEN" in lmr_*) ;; *) die "Isi RESTORE_TOKEN dari tab Restore manual." ;; esac
    case "$TOKEN$BASE_URL" in *[!A-Za-z0-9_:/.-]*) die "LUMBUNG_URL atau RESTORE_TOKEN berisi karakter yang tidak dikenal." ;; esac
    [ -n "$AGE_FILE" ] || die "Isi AGE_IDENTITY_FILE dengan lokasi kunci pemulihan age."
    case "$AGE_FILE" in /*) ;; *) AGE_FILE=$(pwd)/$AGE_FILE ;; esac

    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    trap 'exit 130' INT TERM

    check_server
    request_session
    choose_dir
    DB_MEM_LIMIT_SHOWN="${DB_MEM_LIMIT:-2g} memori dan ${DB_CPUS:-2} CPU"
    cleanup_tmp

    if [ "$(cat "$DIR/state" 2>/dev/null || true)" = "dipulihkan" ]; then
        step "Database sudah hidup dari percobaan sebelumnya; hanya mendaftar"
        BACKUP_PASS=$(env_value BACKUP_PASS "$DIR/.env")
        [ -n "$BACKUP_PASS" ] || die "$DIR/.env tidak berisi BACKUP_PASS; hapus $DIR dan $NAME lalu mulai dari awal."
        register_agent
        cleanup_files
        report_done
        return 0
    fi

    docker container inspect "$NAME" >/dev/null 2>&1 && die "Container bernama $NAME sudah ada di server ini."
    port_busy "$PORT" && die "Port $PORT sudah dipakai di server ini. Buat sesi baru dengan port lain."
    total=$(grep '^FILE ' "$WORK/session" | awk '{s += $4} END {print s + 0}')
    full_size=$(grep '^FILE ' "$WORK/session" | awk '$3 == "fullbackup" {print $4; exit}')
    free_kb=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')
    need_kb=$(( (total + full_size * 5) / 1024 ))
    [ "$free_kb" -ge "$need_kb" ] || die "Ruang disk $DIR kurang: tersedia $((free_kb / 1024)) MB, perlu sekitar $((need_kb / 1024)) MB."
    docker image inspect "$IMAGE" >/dev/null 2>&1 || { step "Mengambil image $IMAGE"; docker pull "$IMAGE" >/dev/null || die "Image $IMAGE tidak bisa diambil."; }

    download_files
    prepare_data
    make_backup_user
    write_env
    step "Menjalankan $NAME"
    start_production
    printf 'dipulihkan\n' > "$DIR/state"
    register_agent
    cleanup_files
    report_done
}

# Dipanggil di baris terakhir: unduhan yang terpotong tidak menjalankan apa pun. Perintah di dalamnya tidak boleh
# membaca stdin, karena stdin sh adalah isi script ini.
main </dev/null
