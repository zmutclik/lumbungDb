#!/bin/bash
# Gagal jika salah satu perintah dalam pipe gagal (bukan hanya gzip)
set -o pipefail

# Cegah backup berjalan dobel (cron + manual, atau backup sebelumnya belum selesai)
exec 9>/run/databasebackup.lock
if ! flock -n 9; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Full backup lain masih berjalan, dibatalkan" >&2
    # Kode sendiri: "sudah berjalan" bukan backup gagal
    exit 75
fi
start_time=$(date +%s)
MYSQL_ARGS="--defaults-extra-file=/etc/db_backup.cnf"
MYSQL="/usr/bin/mariadb $MYSQL_ARGS "
MYSQLBACKUP="/usr/bin/mariadb-backup $MYSQL_ARGS "

BACKUP_DIR="/backup"
BINLOG_DIR="$BACKUP_DIR/binlog"
BACKUP_RETENTION_DAYS=30
LOG_RETENTION_DAYS=365
EXCLUDED_DBS="mysql performance_schema information_schema phpmyadmin sys"

# Buat struktur direktori backup
mkdir -p "$BACKUP_DIR/fullbackup"
mkdir -p "$BACKUP_DIR/log"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOGFILE="$BACKUP_DIR/log/backup_log_$(date +"%Y").log"
# Variabel untuk melacak status backup
BACKUP_FAILED=0
FAILED_DATABASES=""
SUCCESSFUL_DATABASES=""
DELETED_FILESBACKUP=""
# Fungsi logging
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOGFILE"
}
. /opt/backup/binlog_retention.sh
# Fungsi validasi backup
validate_backup() {
    local BACKUP_FILE="$1"

    # Periksa ukuran file
    if [ ! -s "$BACKUP_FILE" ]; then
        log "GAGAL: File Full Backup kosong" "ERROR"
        return 1
    fi

    # Coba unzip dan periksa integritas
    gzip -t "$BACKUP_FILE" 2>/dev/null
    if [ $? -ne 0 ]; then
        log "GAGAL: File Full Backup rusak" "ERROR"
        return 1
    fi

    return 0
}
# Fungsi backup database
backup_databases() {
    local DB_BACKUP_DIR="$BACKUP_DIR/fullbackup"
    local BACKUP_FILE="$DB_BACKUP_DIR/$TIMESTAMP.xbstream.gz"
    # Ditulis ke .tmp lalu rename: agen tidak pernah melihat file setengah jadi dengan nama akhir
    local TMP_FILE="$BACKUP_FILE.tmp"
    log "Proses Full Backup database"
    # Backup database dengan timeout dan penanganan error
    timeout 1h $MYSQLBACKUP --backup --stream=xbstream 2>>"$LOGFILE" | gzip >"$TMP_FILE"

    # Periksa status backup
    if [ $? -eq 0 ]; then
        # Validasi backup
        if validate_backup "$TMP_FILE"; then
            # .info ditulis sebelum nama akhir, supaya agen selalu menemukannya
            [ -n "$BINLOG_START" ] && echo "BINLOG_START=$BINLOG_START" >"$BACKUP_FILE.info"
            mv "$TMP_FILE" "$BACKUP_FILE"
            log "Semua database berhasil di-BACKUP"
        else
            log "PERINGATAN: database gagal di-BACKUP"
            BACKUP_FAILED=1
            rm -f "$TMP_FILE"
        fi
    else
        log "Full Backup gagal"
        BACKUP_FAILED=1
        rm -f "$TMP_FILE"
    fi
}

# Hapus arsip binlog yang sudah tercakup full backup.
# Posisi binlog full backup selalu di binlog yang aktif saat backup dimulai atau sesudahnya,
# jadi binlog dengan nomor lebih kecil tidak dibutuhkan lagi untuk point-in-time recovery.
purge_archived_binlogs() {
    [ -n "$BINLOG_START" ] || return 0
    local START_NUM=$((10#${BINLOG_START##*.}))
    # Penanda untuk binlogbackup.sh agar binlog lama yang masih ada di server tidak diarsipkan ulang
    mkdir -p "$BINLOG_DIR"
    echo "$BINLOG_START" >"$BINLOG_DIR/.full_backup_start"
    local DELETED=0 KEPT=0
    local FILE NAME
    if lumbung_enabled; then
        load_sent_binlogs
    fi

    for FILE in "$BINLOG_DIR"/*.gz; do
        [ -f "$FILE" ] || continue
        NAME=$(basename "$FILE" .gz)
        # mysql-bin.000012.gz: hapus jika sebelum binlog awal full backup.
        # mysql-bin.000012.<waktu>.gz: arsip dari penomoran lama (sebelum restore), selalu dihapus.
        if [[ "$NAME" =~ ^[^.]+\.([0-9]+)$ ]] && [ $((10#${BASH_REMATCH[1]})) -ge $START_NUM ]; then
            continue
        fi
        # Belum sampai di LumbungDB: ditahan, supaya rantai di NAS tidak putus. Batasnya purge_expired_unsent_binlogs.
        if lumbung_enabled && ! binlog_is_sent "$(basename "$FILE")"; then
            KEPT=$((KEPT + 1))
            continue
        fi
        rm -f "$FILE" && DELETED=$((DELETED + 1))
    done
    log "Menghapus $DELETED arsip binlog sebelum $BINLOG_START (sudah tercakup full backup), $KEPT ditahan karena belum terkirim"
}

# Fungsi hapus backup lama
cleanup_old_backups() {
    log "Menghapus backup yang lebih dari $BACKUP_RETENTION_DAYS hari"
    # *.sql.gz untuk file lama sebelum akhiran diganti ke .xbstream.gz
    local DELETED_FILES=$(find "$BACKUP_DIR/fullbackup" \( -name "*.xbstream.gz" -o -name "*.sql.gz" \) -type f -mtime +$BACKUP_RETENTION_DAYS -delete -print | wc -l)
    find "$BACKUP_DIR/fullbackup" -name "*.xbstream.gz.info" -type f -mtime +$BACKUP_RETENTION_DAYS -delete
    find "$BACKUP_DIR/fullbackup" -name "*.gz.tmp" -type f -mmin +180 -delete
    find "$BACKUP_DIR/log" -name "*.log" -type f -mtime +$LOG_RETENTION_DAYS -delete
}
# Fungsi utama
main() {
    log "Memulai proses backup database"

    # Reset variabel
    BACKUP_FAILED=0
    FAILED_DATABASES=""
    SUCCESSFUL_DATABASES=""
    DELETED_FILESBACKUP=""

    # Binlog aktif saat backup dimulai, kosong jika binlog tidak aktif
    BINLOG_START=$($MYSQL -BNe "SHOW BINARY LOGS;" 2>/dev/null | tail -n1 | cut -f1)

    # Jalankan backup
    backup_databases

    # Arsip binlog hanya dihapus jika full backup berhasil
    if [ $BACKUP_FAILED -eq 0 ]; then
        purge_archived_binlogs
    fi
    purge_expired_unsent_binlogs

    # Bersihkan backup lama
    cleanup_old_backups

    log "Proses backup database selesai dalam $(($(date +%s) - start_time)) detik"

    # Kembalikan status untuk integrasi dengan sistem lain
    exit $BACKUP_FAILED
}

# Jalankan script
main
