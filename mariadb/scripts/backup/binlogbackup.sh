#!/bin/bash
# Arsipkan binary log ke /backup/binlog untuk point-in-time recovery.
# Binlog aktif ditutup dulu (FLUSH BINARY LOGS), lalu semua binlog yang sudah tertutup
# dan belum diarsipkan disalin dalam bentuk gzip.
set -o pipefail

# Cegah arsip binlog berjalan dobel
exec 9>/run/binlogbackup.lock
if ! flock -n 9; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Arsip binlog lain masih berjalan, dibatalkan" >&2
    # Kode sendiri: "sudah berjalan" bukan backup gagal
    exit 75
fi
start_time=$(date +%s)
MYSQL_ARGS="--defaults-extra-file=/etc/db_backup.cnf"
MYSQL="/usr/bin/mariadb $MYSQL_ARGS "

DATA_DIR="/var/lib/mysql"
BACKUP_DIR="/backup"
BINLOG_DIR="$BACKUP_DIR/binlog"
BACKUP_RETENTION_DAYS=30
LOG_RETENTION_DAYS=365
# Binlog aktif terakhir yang di-flush, agar tidak flush ulang jika belum ada perubahan
STATE_FILE="$BINLOG_DIR/.last_active"
# Binlog awal full backup terakhir (ditulis databasebackup.sh), binlog sebelumnya tidak perlu diarsipkan
FULL_START_FILE="$BINLOG_DIR/.full_backup_start"

mkdir -p "$BINLOG_DIR"
mkdir -p "$BACKUP_DIR/log"
LOGFILE="$BACKUP_DIR/log/binlog_log_$(date +"%Y-%m").log"
BACKUP_FAILED=0
ARCHIVED=0

# Fungsi logging
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOGFILE"
}
. /opt/backup/binlog_retention.sh

# Daftar binlog "nama ukuran", baris terakhir adalah binlog aktif
list_binlogs() {
    $MYSQL -BNe "SHOW BINARY LOGS;" 2>>"$LOGFILE" | awk '{print $1, $2}'
}

# Salin satu binlog yang sudah tertutup ke arsip
archive_binlog() {
    local NAME="$1"
    local SIZE="$2"
    local SRC="$DATA_DIR/$NAME"
    local DEST="$BINLOG_DIR/$NAME.gz"

    if [ -f "$DEST" ]; then
        # Ukuran asli sama: sudah diarsipkan sebelumnya
        if [ "$(gzip -l "$DEST" | awk 'NR==2 {print $2}')" = "$SIZE" ]; then
            return 0
        fi
        # Nama sama tapi isi beda: penomoran binlog mulai ulang (mis. setelah restore)
        local OLD="$BINLOG_DIR/$NAME.$(date -r "$DEST" +%Y%m%d_%H%M%S).gz"
        log "PERINGATAN: $NAME sudah ada di arsip dengan isi berbeda, arsip lama dipindah ke $(basename "$OLD")"
        mv "$DEST" "$OLD"
    fi

    if [ ! -f "$SRC" ]; then
        log "GAGAL: $SRC tidak ditemukan"
        BACKUP_FAILED=1
        return 1
    fi

    # Tulis ke file sementara agar arsip setengah jadi tidak dianggap lengkap
    if nice -n 19 ionice -c3 gzip -c "$SRC" >"$DEST.tmp" && gzip -t "$DEST.tmp"; then
        mv "$DEST.tmp" "$DEST"
        ARCHIVED=$((ARCHIVED + 1))
    else
        log "GAGAL: arsip $NAME gagal"
        BACKUP_FAILED=1
        rm -f "$DEST.tmp"
        return 1
    fi
}

archive_binlogs() {
    local BINLOGS
    if ! BINLOGS=$(list_binlogs) || [ -z "$BINLOGS" ]; then
        log "GAGAL: tidak bisa mengambil daftar binlog (binlog belum aktif atau cek login user backup)"
        BACKUP_FAILED=1
        return
    fi

    # Tutup binlog aktif hanya jika ada perubahan sejak flush terakhir
    if [ "$(echo "$BINLOGS" | tail -n1)" != "$(cat "$STATE_FILE" 2>/dev/null)" ]; then
        if ! $MYSQL -e "FLUSH BINARY LOGS;" 2>>"$LOGFILE"; then
            log "GAGAL: FLUSH BINARY LOGS gagal"
            BACKUP_FAILED=1
            return
        fi
        if ! BINLOGS=$(list_binlogs); then
            log "GAGAL: tidak bisa mengambil daftar binlog setelah flush"
            BACKUP_FAILED=1
            return
        fi
        echo "$BINLOGS" | tail -n1 >"$STATE_FILE"
    fi

    local FULL_START_NUM=0
    if [ -f "$FULL_START_FILE" ]; then
        local FULL_START ACTIVE
        FULL_START=$(cat "$FULL_START_FILE")
        ACTIVE=$(echo "$BINLOGS" | tail -n1 | cut -d' ' -f1)
        FULL_START_NUM=$((10#${FULL_START##*.}))
        # Penanda lebih besar dari binlog aktif: penomoran mulai ulang (mis. setelah restore), abaikan
        if [ $FULL_START_NUM -gt $((10#${ACTIVE##*.})) ]; then
            log "PERINGATAN: penomoran binlog mulai ulang, penanda full backup $FULL_START diabaikan"
            rm -f "$FULL_START_FILE"
            FULL_START_NUM=0
        fi
    fi

    # Semua kecuali binlog aktif, mulai dari binlog awal full backup terakhir
    while read -r NAME SIZE; do
        [ $((10#${NAME##*.})) -ge $FULL_START_NUM ] || continue
        archive_binlog "$NAME" "$SIZE"
    done < <(echo "$BINLOGS" | head -n -1)
}

# Fungsi hapus arsip lama
cleanup_old_backups() {
    find "$BINLOG_DIR" -name "*.gz" -type f -mtime +$BACKUP_RETENTION_DAYS -delete
    find "$BINLOG_DIR" -name "*.gz.tmp" -type f -mmin +60 -delete
    find "$BACKUP_DIR/log" -name "*.log" -type f -mtime +$LOG_RETENTION_DAYS -delete
}

# Fungsi utama
main() {
    archive_binlogs
    # Di sini juga, supaya batasnya tetap berlaku saat jadwal full dimatikan
    purge_expired_unsent_binlogs
    cleanup_old_backups

    # Log hanya jika ada yang diarsipkan atau gagal, karena script ini berjalan sering
    if [ $BACKUP_FAILED -ne 0 ] || [ $ARCHIVED -gt 0 ]; then
        log "Arsip binlog: $ARCHIVED file baru dalam $(($(date +%s) - start_time)) detik"
    fi

    exit $BACKUP_FAILED
}

# Jalankan script
main
