#!/bin/bash
# Fungsi bersama databasebackup.sh dan binlogbackup.sh: arsip binlog lokal hanya dihapus bila sudah
# terkirim ke LumbungDB, atau bila sudah melewati BINLOG_UNSENT_MAX_DAYS. Di-source, memakai log() dan BINLOG_DIR.
BINLOG_UNSENT_DEFAULT_DAYS=3
SENT_FILE="/backup/.lumbung/agent/sent.json"
[ -r /etc/lumbung/backup.env ] && . /etc/lumbung/backup.env

lumbung_enabled() {
    [ "$LUMBUNG_ENABLED" = "1" ]
}

binlog_unsent_max_days() {
    if [[ "$BINLOG_UNSENT_MAX_DAYS" =~ ^[0-9]+$ ]] && [ "$((10#$BINLOG_UNSENT_MAX_DAYS))" -ge 1 ]; then
        echo "$((10#$BINLOG_UNSENT_MAX_DAYS))"
    else
        echo "$BINLOG_UNSENT_DEFAULT_DAYS"
    fi
}

# Tulis nama arsip binlog yang tercatat terkirim dengan ukuran dan waktu ubah yang sama seperti file sekarang.
# Isi sent.json milik user lumbung, jadi hanya dibaca sebagai data.
load_sent_binlogs() {
    SENT_BINLOGS=$(python3 -I -c '
import json, os, sys
try:
    with open(sys.argv[1]) as handle:
        sent = json.load(handle)
except (OSError, ValueError):
    sys.exit(0)
if not isinstance(sent, dict):
    sys.exit(0)
for key, record in sent.items():
    if not key.startswith("binlog/") or not isinstance(record, dict):
        continue
    name = key[len("binlog/"):]
    try:
        stat = os.stat(os.path.join(sys.argv[2], name))
    except OSError:
        continue
    if record.get("size") == stat.st_size and record.get("mtime") == int(stat.st_mtime):
        print(name)
' "$SENT_FILE" "$BINLOG_DIR" 2>/dev/null)
}

binlog_is_sent() {
    grep -qxF -- "$1" <<<"$SENT_BINLOGS"
}

# Arsip yang belum terkirim melewati batas tetap dihapus supaya /backup tidak penuh; rantai di NAS putus.
purge_expired_unsent_binlogs() {
    lumbung_enabled || return 0
    local MAX_DAYS FILE NAME
    MAX_DAYS=$(binlog_unsent_max_days)
    load_sent_binlogs
    while IFS= read -r FILE; do
        [ -n "$FILE" ] || continue
        NAME=$(basename "$FILE")
        binlog_is_sent "$NAME" && continue
        if rm -f "$FILE"; then
            log "PERINGATAN: arsip $NAME belum terkirim lebih dari $MAX_DAYS hari dan dihapus, rantai binlog di NAS putus"
        fi
    done < <(find "$BINLOG_DIR" -maxdepth 1 -name "*.gz" -type f -mmin +$((MAX_DAYS * 1440)))
}
