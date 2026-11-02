#!/bin/bash
# Membuat user backup dari env BACKUP_USER / BACKUP_PASS.
# File ini di-source oleh docker-entrypoint.sh resmi MariaDB (hanya saat inisialisasi pertama),
# sehingga fungsi docker_process_sql dan docker_sql_escape_string_literal tersedia.
if [ -z "$BACKUP_PASS" ]; then
    mysql_warn "BACKUP_PASS kosong, user backup tidak dibuat"
else
    backupUserEscaped=$(docker_sql_escape_string_literal "${BACKUP_USER:-user_backup}")
    backupPassEscaped=$(docker_sql_escape_string_literal "$BACKUP_PASS")
    mysql_note "Membuat user backup '${BACKUP_USER:-user_backup}'@'localhost'"
    docker_process_sql --database=mysql <<-EOSQL
		CREATE USER '$backupUserEscaped'@'localhost' IDENTIFIED BY '$backupPassEscaped';
		GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER, RELOAD, PROCESS, BINLOG MONITOR ON *.* TO '$backupUserEscaped'@'localhost';
		FLUSH PRIVILEGES;
	EOSQL
    unset backupUserEscaped backupPassEscaped
fi
