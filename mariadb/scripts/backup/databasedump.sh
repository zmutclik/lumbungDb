#!/bin/bash
# Pemakaian: databasedump.sh [DATABASE]. Tanpa argumen: semua database dan _users. "_users": hanya user & grant.
# Gagal jika salah satu perintah dalam pipe gagal (bukan hanya gzip)
set -o pipefail

ONLY_DB="${1:-}"
if [ -n "$ONLY_DB" ] && ! [[ "$ONLY_DB" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
    echo "Nama database tidak valid" >&2
    exit 2
fi

# Cegah dump berjalan dobel (cron + manual, atau dump sebelumnya belum selesai)
exec 9>/run/databasedump.lock
if ! flock -n 9; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Dump lain masih berjalan, dibatalkan" >&2
    # Kode sendiri: "sudah berjalan" bukan backup gagal
    exit 75
fi
start_time=$(date +%s)
MYSQL_ARGS="--defaults-extra-file=/etc/db_backup.cnf"
MYSQL="/usr/bin/mariadb $MYSQL_ARGS "
MYSQLDUMP="/usr/bin/mariadb-dump $MYSQL_ARGS "

BACKUP_DIR="/backup"
BACKUP_RETENTION_DAYS=30
LOG_RETENTION_DAYS=365
EXCLUDED_DBS="mysql performance_schema information_schema phpmyadmin sys"
# Folder dump user & grant, diawali "_" agar tidak bentrok dengan nama database
USERS_DUMP_NAME="_users"

# Buat struktur direktori backup
mkdir -p "$BACKUP_DIR/databases"
mkdir -p "$BACKUP_DIR/log"
# Tanggal untuk nama file backup
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
# Log file untuk mencatat proses backup
LOGFILE="$BACKUP_DIR/log/dump_log_$(date +"%Y-%m").log"
# Variabel untuk melacak status backup
BACKUP_FAILED=0
FAILED_DATABASES=""
SUCCESSFUL_DATABASES=""
DELETED_FILESBACKUP=""

# Fungsi logging
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOGFILE"
}

# Fungsi validasi backup
validate_backup() {
    local BACKUP_FILE="$1"
    local DB_NAME="$2"

    # Periksa ukuran file
    if [ ! -s "$BACKUP_FILE" ]; then
        log "GAGAL: Backup $DB_NAME kosong" "ERROR"
        return 1
    fi

    # Coba unzip dan periksa integritas
    gzip -t "$BACKUP_FILE" 2>/dev/null
    if [ $? -ne 0 ]; then
        log "GAGAL: Backup $DB_NAME rusak" "ERROR"
        return 1
    fi

    return 0
}
# Dump user & grant (database mysql tidak ikut di-dump)
backup_users() {
    local USERS_DIR="$BACKUP_DIR/databases/$USERS_DUMP_NAME"
    mkdir -p "$USERS_DIR"
    local BACKUP_FILE="$USERS_DIR/$USERS_DUMP_NAME-$TIMESTAMP.sql.gz"

    log "Proses backup user & grant"
    # --insert-ignore: CREATE USER IF NOT EXISTS, user yang sudah ada tidak ditimpa saat restore
    timeout 1h $MYSQLDUMP --system=users --insert-ignore 2>>"$LOGFILE" | gzip >"$BACKUP_FILE.tmp"

    if [ $? -eq 0 ] && validate_backup "$BACKUP_FILE.tmp" "user & grant"; then
        mv "$BACKUP_FILE.tmp" "$BACKUP_FILE"
        log "Backup user & grant berhasil"
        SUCCESSFUL_DATABASES+=" $USERS_DUMP_NAME"
    else
        log "Backup user & grant gagal"
        BACKUP_FAILED=$((BACKUP_FAILED + 1))
        FAILED_DATABASES+=" $USERS_DUMP_NAME"
        rm -f "$BACKUP_FILE.tmp"
    fi
}
# Fungsi backup database
backup_databases() {
    # Dapatkan daftar semua database
    local DATABASES
    if ! DATABASES=$($MYSQL -BNe "SHOW DATABASES;" 2>>"$LOGFILE"); then
        log "GAGAL: tidak bisa mengambil daftar database (cek login user backup)"
        BACKUP_FAILED=1
        return
    fi
    DATABASES=$(echo "$DATABASES" | grep -Ev "^(${EXCLUDED_DBS// /|})$")
    if [ -n "$ONLY_DB" ]; then
        if ! grep -qxF -- "$ONLY_DB" <<<"$DATABASES"; then
            log "GAGAL: database $ONLY_DB tidak ditemukan"
            BACKUP_FAILED=1
            return
        fi
        DATABASES="$ONLY_DB"
    fi

    log "Memulai dump per database"

    # Buat backup untuk setiap database
    for DB in $DATABASES; do
        # Buat nama file unik untuk setiap database
        local DB_BACKUP_DIR="$BACKUP_DIR/databases/$DB"
        mkdir -p "$DB_BACKUP_DIR"
        local BACKUP_FILE="$DB_BACKUP_DIR/$DB-$TIMESTAMP.sql.gz"

        log "Proses backup database: $DB"

        # Backup database dengan timeout dan penanganan error
        timeout 1h $MYSQLDUMP \
            --single-transaction \
            --routines \
            --triggers \
            --events \
            --databases "$DB" 2>>"$LOGFILE" | gzip >"$BACKUP_FILE.tmp"

        # Periksa status backup
        if [ $? -eq 0 ]; then
            # Validasi backup
            if validate_backup "$BACKUP_FILE.tmp" "$DB"; then
                # Nama akhir hanya untuk file yang sudah lolos validasi
                mv "$BACKUP_FILE.tmp" "$BACKUP_FILE"
                log "Backup $DB berhasil"
                SUCCESSFUL_DATABASES+=" $DB"
            else
                log "Backup $DB gagal validasi"
                BACKUP_FAILED=$((BACKUP_FAILED + 1))
                FAILED_DATABASES+=" $DB"
                rm -f "$BACKUP_FILE.tmp"
            fi
        else
            log "Backup $DB gagal"
            BACKUP_FAILED=$((BACKUP_FAILED + 1))
            FAILED_DATABASES+=" $DB"
            rm -f "$BACKUP_FILE.tmp"
        fi
    done

    # Dump satu database tidak ikut membuat dump user & grant
    [ -z "$ONLY_DB" ] && backup_users

    # Ringkasan hasil backup
    if [ $BACKUP_FAILED -gt 0 ]; then
        log "PERINGATAN: $BACKUP_FAILED database gagal di-DUMP:$FAILED_DATABASES"
        log "Database berhasil di-DUMP:$SUCCESSFUL_DATABASES"
    else
        log "Semua database berhasil di-backup"
    fi
}

# Fungsi hapus backup lama
cleanup_old_backups() {
    log "Menghapus backup yang lebih dari $BACKUP_RETENTION_DAYS hari"
    # Dapatkan daftar semua database
    local DATABASES=$($MYSQL -BNe "SHOW DATABASES;" 2>>"$LOGFILE" | grep -Ev "^(${EXCLUDED_DBS// /|})$")
    # Buat backup untuk setiap database
    for DB in $DATABASES; do
        # Buat nama file unik untuk setiap database
        # Hapus file backup lama per database
        local DELETED_FILES=$(find "$BACKUP_DIR/databases/$DB" -name "*.sql.gz" -type f -mtime +$BACKUP_RETENTION_DAYS -delete -print | wc -l)

        DELETED_FILESBACKUP+=" $DELETED_FILES"
    done
    find "$BACKUP_DIR/databases/$USERS_DUMP_NAME" -name "*.sql.gz" -type f -mtime +$BACKUP_RETENTION_DAYS -delete
    find "$BACKUP_DIR/databases" -name "*.gz.tmp" -type f -mmin +180 -delete
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

    # Jalankan backup
    if [ "$ONLY_DB" = "$USERS_DUMP_NAME" ]; then
        backup_users
    else
        backup_databases
    fi

    # Bersihkan backup lama
    cleanup_old_backups

    log "Proses backup database selesai dalam $(($(date +%s) - start_time)) detik"

    # Kembalikan status untuk integrasi dengan sistem lain
    exit $BACKUP_FAILED
}

# Jalankan script
main
