#!/bin/bash
# Tulis kredensial backup untuk cron dari env, bukan dari image
if [ -n "$BACKUP_PASS" ]; then
    backup_user="${BACKUP_USER:-user_backup}"
    backup_pass="${BACKUP_PASS//\\/\\\\}"
    backup_pass="${backup_pass//\"/\\\"}"
    (
        umask 077
        printf '[client]\nuser = %s\npassword = "%s"\n' "$backup_user" "$backup_pass" >/etc/db_backup.cnf
    )
    unset backup_user backup_pass
else
    echo "BACKUP_PASS kosong, /etc/db_backup.cnf tidak dibuat (backup akan gagal)" >&2
fi

# Folder status LumbungDB. Volume lama belum memilikinya, jadi disiapkan tiap start.
mkdir -p /backup/.lumbung/agent /run/lumbung /etc/lumbung
chown root:root /backup/.lumbung
chmod 755 /backup/.lumbung
chown -R lumbung:lumbung /backup/.lumbung/agent
chmod 700 /backup/.lumbung/agent

# User lumbung tidak boleh membaca datadir, termasuk binlog di dalamnya.
chmod 750 /var/lib/mysql 2>/dev/null

# Script backup dijalankan cron dengan env minimal, jadi nilainya ditulis ke berkas tanpa rahasia.
unsent_days=3
if [[ "$BINLOG_UNSENT_MAX_DAYS" =~ ^[0-9]+$ ]] && [ "$((10#$BINLOG_UNSENT_MAX_DAYS))" -ge 1 ]; then
    unsent_days=$((10#$BINLOG_UNSENT_MAX_DAYS))
fi
{
    [ -n "$LUMBUNG_URL" ] && echo "LUMBUNG_ENABLED=1"
    echo "BINLOG_UNSENT_MAX_DAYS=$unsent_days"
} >/etc/lumbung/backup.env
chmod 644 /etc/lumbung/backup.env

/opt/tuning.sh
/opt/backup/install_cron.sh

# Database tetap dijalankan walau cron gagal start, backup saja yang tidak jalan
if ! service cron start; then
    echo "Gagal menjalankan cron, backup otomatis tidak aktif" >&2
fi

# Agen hanya berjalan bila LUMBUNG_URL terisi. Salinan hasil restore dijalankan tanpa LUMBUNG_*,
# jadi tidak mengirim heartbeat atas nama container asli. Kegagalan agen tidak menghalangi MariaDB.
if [ -n "$LUMBUNG_URL" ]; then
    (
        while true; do
            # env -i: agen tidak mewarisi password database
            gosu lumbung env -i PATH=/usr/bin:/bin HOME=/backup/.lumbung/agent LANG=C.UTF-8 \
                LUMBUNG_URL="$LUMBUNG_URL" LUMBUNG_TOKEN="$LUMBUNG_TOKEN" \
                BACKUP_DIR="${BACKUP_DIR:-/backup}" \
                INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}" \
                STABLE_SECONDS="${STABLE_SECONDS:-120}" \
                MAX_FILE_AGE_DAYS="${MAX_FILE_AGE_DAYS:-0}" \
                python3 -I /opt/lumbung/agent.py
            echo "Agen berhenti dengan kode $?, dijalankan lagi dalam 10 detik" >&2
            sleep 10
        done
    ) &
fi

exec docker-entrypoint.sh "$@"
