#!/bin/bash
# LUMBUNG_MODE=sidecar: MariaDB berjalan di container lain (image lain atau tag zmutclik/mariadb lama). Container ini
# hanya menjalankan cron backup dan agen, berbagi namespace jaringan dan PID dengan container itu, dan memasang
# volume datanya read-only lewat volumes_from. Lihat docker-compose.sidecar.yml.
SIDECAR=0
if [ "$LUMBUNG_MODE" = "sidecar" ]; then
    SIDECAR=1
    if ! [[ "${DB_PORT:-3306}" =~ ^[0-9]{1,5}$ ]]; then
        echo "DB_PORT tidak valid" >&2
        exit 1
    fi
fi

# Nilai ditulis di antara tanda kutip ganda dengan \ dan " di-escape.
cnf_value() {
    local value="${1//\\/\\\\}"
    printf '"%s"' "${value//\"/\\\"}"
}

# Tulis kredensial backup untuk cron dari env, bukan dari image
if [ -n "$BACKUP_PASS" ]; then
    (
        umask 077
        {
            printf '[client]\nuser = %s\npassword = %s\n' "${BACKUP_USER:-user_backup}" "$(cnf_value "$BACKUP_PASS")"
            # 127.0.0.1, bukan localhost: localhost berarti socket, sedangkan socket MariaDB ada di container lain
            [ "$SIDECAR" = 1 ] && printf 'host = 127.0.0.1\nport = %s\nprotocol = tcp\n' "${DB_PORT:-3306}"
        } >/etc/db_backup.cnf
    )
else
    echo "BACKUP_PASS kosong, /etc/db_backup.cnf tidak dibuat (backup akan gagal)" >&2
fi

# Folder status LumbungDB. Volume lama belum memilikinya, jadi disiapkan tiap start.
mkdir -p /backup/.lumbung/agent /run/lumbung /etc/lumbung
chown root:root /backup/.lumbung
chmod 755 /backup/.lumbung
chown -R lumbung:lumbung /backup/.lumbung/agent
chmod 700 /backup/.lumbung/agent

# User lumbung tidak boleh membaca datadir, termasuk binlog di dalamnya. Di mode sidecar datadir read-only dan milik
# container lain; izinnya diperiksa, tidak diubah.
if [ "$SIDECAR" = 0 ]; then
    chmod 750 /var/lib/mysql 2>/dev/null
elif [ -n "$(gosu lumbung find /var/lib/mysql -maxdepth 2 -type f -readable ! -name "*upgrade_info" -print -quit 2>/dev/null)" ]; then
    echo "PERINGATAN: ada file datadir yang terbaca oleh user lumbung; jalankan chmod 750 /var/lib/mysql di container MariaDB" >&2
fi

# Password user metrik untuk agen di mode sidecar: unix_socket tidak bisa dipakai lewat TCP.
rm -f /etc/lumbung/metrics.cnf
if [ "$SIDECAR" = 1 ]; then
    if [ -n "$LUMBUNG_METRICS_PASS" ]; then
        (
            umask 077
            printf '[client]\nuser = lumbung\npassword = %s\nhost = 127.0.0.1\nport = %s\n' \
                "$(cnf_value "$LUMBUNG_METRICS_PASS")" "${DB_PORT:-3306}" >/etc/lumbung/metrics.cnf
        )
        chown lumbung:lumbung /etc/lumbung/metrics.cnf
    else
        echo "LUMBUNG_METRICS_PASS kosong, metrik MariaDB tidak dikirim" >&2
    fi
fi

# Script backup dijalankan cron dengan env minimal, jadi nilainya ditulis ke berkas tanpa rahasia.
unsent_days=3
if [[ "$BINLOG_UNSENT_MAX_DAYS" =~ ^[0-9]+$ ]] && [ "$((10#$BINLOG_UNSENT_MAX_DAYS))" -ge 1 ]; then
    unsent_days=$((10#$BINLOG_UNSENT_MAX_DAYS))
fi
{
    [ -n "$LUMBUNG_URL" ] && echo "LUMBUNG_ENABLED=1"
    [ "$SIDECAR" = 1 ] && echo "LUMBUNG_MODE=sidecar"
    echo "BINLOG_UNSENT_MAX_DAYS=$unsent_days"
} >/etc/lumbung/backup.env
chmod 644 /etc/lumbung/backup.env

# Sidecar tidak menyentuh config MariaDB target, jadi tuning dilewati.
if [ "$SIDECAR" = 0 ]; then
    /opt/tuning.sh
fi
/opt/backup/install_cron.sh

# Database tetap dijalankan walau cron gagal start, backup saja yang tidak jalan.
# Sidecar: cron jadi anak shell ini, bukan daemon. Proses yatim pindah ke PID 1 namespace, yaitu mariadbd di container
# lain yang tidak memungut proses zombie.
if [ "$SIDECAR" = 1 ]; then
    cron -f &
elif ! service cron start; then
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
                LUMBUNG_MODE="${LUMBUNG_MODE:-}" DB_PORT="${DB_PORT:-3306}" \
                LUMBUNG_AGE_RECIPIENTS="${LUMBUNG_AGE_RECIPIENTS:-}" \
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

if [ "$SIDECAR" = 1 ]; then
    # Tidak ada mariadbd di sini. Container ini ikut berhenti saat container MariaDB berhenti, karena berbagi namespace
    # PID; restart: always menjalankannya lagi setelah MariaDB hidup.
    trap 'exit 0' TERM INT
    while true; do
        sleep 3600 &
        wait $!
    done
fi

exec docker-entrypoint.sh "$@"
