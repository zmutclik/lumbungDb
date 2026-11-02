#!/bin/bash
# Pasang jadwal cron backup. Dijalankan entrypoint setiap container start,
# crontab ditulis ulang (bukan ditambah) sehingga tidak pernah dobel.
#
# Urutan jadwal: /backup/.lumbung/schedule.json dari LumbungDB (divalidasi dulu), lalu
# BACKUP_CRON / DUMP_CRON / BINLOG_CRON (cron 5 kolom atau "off"), lalu bawaan.
# Bawaan: menit & jam diturunkan dari hostname, jadi tiap container berbeda tapi tetap stabil.
# Binlog bawaan tiap 15 menit.

SEED=$(hostname | cksum | cut -d' ' -f1)
DEFAULT_BACKUP_CRON="$((SEED % 60)) $((SEED / 60 % 2)) * * *"
DEFAULT_DUMP_CRON="$((SEED / 120 % 60)) $((SEED / 7200 % 2 + 3)) * * *"
DEFAULT_BINLOG_CRON="*/15 * * * *"

mkdir -p /backup/log

python3 -I /opt/lumbung/schedule.py install \
    "$BACKUP_CRON" "$DUMP_CRON" "$BINLOG_CRON" \
    "$DEFAULT_BACKUP_CRON" "$DEFAULT_DUMP_CRON" "$DEFAULT_BINLOG_CRON"
