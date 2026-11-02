#!/bin/bash
# Pemulihan sungguhan di container sementara, dari file yang ada di NAS. Tidak menyentuh container sumber.
#   restore_check.sh full <full> <binlog...>   prepare, naikkan MariaDB, terapkan binlog, cetak isi tabel
#   restore_check.sh dump <dump.sql.gz>        impor ke MariaDB kosong, cetak isi tabel
set -euo pipefail
mode="$1"; shift
target=/tmp/pulih
mkdir -p "$target"
start_server() {
  mariadbd --user=root --datadir="$target" --socket=/tmp/s.sock --skip-networking --skip-grant-tables \
    --event-scheduler=OFF --log-bin=OFF >/tmp/server.log 2>&1 &
  for _ in $(seq 1 90); do mariadb -S /tmp/s.sock -e "select 1" >/dev/null 2>&1 && return 0; sleep 1; done
  tail -20 /tmp/server.log; exit 3
}
if [ "$mode" = "full" ]; then
  full="$1"; shift
  zcat "/nas/$full" | mbstream -x -C "$target"
  mariadb-backup --prepare --target-dir="$target" >/tmp/prepare.log 2>&1 || { tail -5 /tmp/prepare.log; exit 2; }
  read -r first_file position _ < "$target/mariadb_backup_binlog_info"
  echo "posisi: $first_file $position"
  start_server
  echo "isi_full: $(mariadb -S /tmp/s.sock -BNe 'select count(*) from appdb.t')"
  args=(--start-position="$position")
  for binlog in "$@"; do
    zcat "/nas/$binlog" > "/tmp/$(basename "$binlog").bin"
    mariadb-binlog "${args[@]}" "/tmp/$(basename "$binlog").bin" | mariadb -S /tmp/s.sock
    args=()
  done
  echo "isi_binlog: $(mariadb -S /tmp/s.sock -BNe 'select count(*) from appdb.t')"
else
  mariadb-install-db --user=root --datadir="$target" >/dev/null 2>&1
  start_server
  zcat "/nas/$1" | mariadb -S /tmp/s.sock
  echo "isi_dump: $(mariadb -S /tmp/s.sock -BNe 'select count(*) from appdb.t')"
fi
