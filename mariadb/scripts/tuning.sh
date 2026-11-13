#!/bin/bash
# Tulis konfigurasi tuning MariaDB sesuai resource container. Dijalankan entrypoint setiap container start,
# jadi kalau limit container diubah, config ikut menyesuaikan setelah restart. Perhitungannya ada di
# /opt/lumbung/tuning.py, yang juga dipakai saat tuning diubah dari LumbungDB.
#
# Urutan: tuning dari LumbungDB (/backup/.lumbung/tuning.json), lalu env berikut:
# DB_TUNING          : auto (default) | off
# DB_MEMORY          : jatah memori untuk MariaDB, mis. 4G / 4096M. Kosong = dari limit container
# DB_MAX_CONNECTIONS : default 200
# DB_DISK            : ssd (default) | hdd
#
# Nilai yang ingin dipaksa manual: mount file .cnf yang namanya urut setelah zz-tuning.cnf (mis. zzz-custom.cnf).

# Database tetap dijalankan walau tuning gagal, dengan config default
if ! python3 -I /opt/lumbung/tuning.py install; then
    rm -f /etc/mysql/conf.d/zz-tuning.cnf
    echo "Tuning gagal, MariaDB jalan dengan config default" >&2
fi
exit 0
