# MariaDB dengan Backup Otomatis

Image Docker MariaDB (`zmutclik/mariadb`) dengan tambahan:

- **Backup otomatis via cron**: full backup fisik (`mariadb-backup`) dan dump logis per database (`mariadb-dump`), retensi 30 hari.
- **Binary log diarsipkan tiap 15 menit** untuk point-in-time recovery (restore sampai detik tertentu).
- **Zona waktu Asia/Jakarta** dan `event_scheduler=ON`.
- **Kredensial tidak disimpan di image**: user backup dibuat dari environment saat inisialisasi pertama.
- **Tuning otomatis** sesuai limit CPU & memori container.
- **Healthcheck** dan upgrade otomatis saat versi MariaDB dinaikkan.

## Mulai cepat

```bash
./env.sh                      # buat .env dengan user & password acak
nano .env                     # sesuaikan CONTAINER_NAME, HOSTNAME, DATA_PORT, DNS, dll.
docker compose up -d --build  # build lokal
```

Untuk memakai image dari Docker Hub tanpa build (mis. lewat Portainer):

```bash
docker compose -f docker-compose_stack.yml up -d
```

`env.sh` menolak menimpa `.env` yang sudah ada, karena password di dalamnya mungkin sudah dipakai database.
Gunakan `./env.sh --force` hanya untuk instalasi baru.

## Variabel `.env`

| Variabel | Keterangan |
|---|---|
| `CONTAINER_NAME`, `HOSTNAME` | Nama container dan hostname. Hostname juga menentukan jadwal backup default |
| `DATA_PORT` | Port di host yang diteruskan ke 3306 |
| `DB_ROOTPASSWORD` | Password root MariaDB |
| `DB_NAME`, `DB_APPUSER`, `DB_APPPASS` | Database dan user aplikasi yang dibuat saat inisialisasi pertama |
| `DNS1`, `DNS2` | DNS server untuk container |
| `DB_CPUS`, `DB_MEM_LIMIT` | Batas CPU dan memori container (default `2` dan `2G`), dasar perhitungan [tuning](#tuning-otomatis) |
| `DB_TUNING`, `DB_MEMORY`, `DB_MAX_CONNECTIONS`, `DB_DISK` | Pengaturan [tuning otomatis](#tuning-otomatis) |
| `BACKUP_USER`, `BACKUP_PASS` | User yang dipakai script backup |
| `BACKUP_CRON`, `DUMP_CRON`, `BINLOG_CRON` | Jadwal format cron (mis. `30 1 * * *`). Kosong = otomatis, `off` = nonaktif |

Variabel `DB_*` dan `BACKUP_USER`/`BACKUP_PASS` hanya dipakai saat volume data masih kosong.
Mengubahnya setelah itu tidak mengubah user di database; lihat [Mengganti password backup](#mengganti-password-backup).

## Tuning otomatis

Setiap container start, `/opt/tuning.sh` membaca limit CPU & memori container lalu menulis `/etc/mysql/conf.d/zz-tuning.cnf`.
Tanpa tuning, MariaDB memakai buffer pool default 128MB yang terlalu kecil untuk database besar.
Hasil perhitungan tampil di `docker logs <container>`.

| Variabel | Default | Keterangan |
|---|---|---|
| `DB_MEM_LIMIT`, `DB_CPUS` | `2G`, `2` | Limit container (`deploy.resources.limits`). Ubah ini sesuai spek server |
| `DB_TUNING` | `auto` | `off` = tidak ada tuning, MariaDB memakai config default |
| `DB_MEMORY` | kosong | Jatah memori MariaDB jika ingin lebih kecil dari limit container |
| `DB_MAX_CONNECTIONS` | `200` | Jumlah koneksi maksimal. Tiap koneksi dijatah ±2MB, diambil dari buffer pool |
| `DB_DISK` | `ssd` | `ssd` atau `hdd`, menentukan kecepatan InnoDB menulis halaman ke disk |

Yang dihitung:

| Parameter | Nilai |
|---|---|
| `innodb_buffer_pool_size` | 50% memori (< 2G), 65% (< 8G), 75% (≥ 8G), dikurangi jatah koneksi |
| `innodb_log_file_size` | 25% buffer pool, 96M–4G |
| `innodb_read/write_io_threads` | Jumlah CPU, 4–16 |
| `innodb_io_capacity(_max)` | `ssd` 2000/4000, `hdd` 200/400 |
| `tmp_table_size`, `max_heap_table_size` | 1/64 memori, 16M–256M |
| `table_open_cache`, `table_definition_cache` | 10 × `max_connections`, 2000–10000 |

Jika container tidak punya limit memori dan `DB_MEMORY` kosong, yang terbaca adalah RAM seluruh host.
Karena itu dipakai 50% RAM host dan muncul peringatan di log.

Mengubah limit cukup dengan edit `.env` lalu `docker compose up -d`. Config dihitung ulang saat container dibuat ulang.
Untuk memaksa nilai tertentu, mount file `.cnf` yang namanya urut setelah `zz-tuning.cnf`:

```yaml
    volumes:
      - ./custom.cnf:/etc/mysql/conf.d/zzz-custom.cnf:ro
```

Melihat nilai yang aktif:

```bash
docker exec <container> cat /etc/mysql/conf.d/zz-tuning.cnf
docker exec <container> sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -e "SELECT @@innodb_buffer_pool_size/1024/1024 AS buffer_pool_mb"'
```

## Backup

| Jenis | Script | Lokasi | Jadwal default |
|---|---|---|---|
| Full backup (fisik) | `/opt/backup/databasebackup.sh` | `/backup/fullbackup/<waktu>.xbstream.gz` | Jam 00:00–01:59 |
| Dump per database | `/opt/backup/databasedump.sh` | `/backup/databases/<db>/<db>-<waktu>.sql.gz` | Jam 03:00–04:59 |
| Dump user & grant | (ikut `databasedump.sh`) | `/backup/databases/_users/_users-<waktu>.sql.gz` | Bersama dump |
| Arsip binary log | `/opt/backup/binlogbackup.sh` | `/backup/binlog/mysql-bin.<nomor>.gz` | Tiap 15 menit |

Menit dan jam default diturunkan dari hostname, sehingga tiap server berbeda tetapi tetap sama setiap restart.
Database `mysql`, `performance_schema`, `information_schema`, `phpmyadmin`, dan `sys` tidak ikut di-dump.

`/backup` adalah volume tersendiri. Sebaiknya letakkan di disk lain dari data volume, atau salin isinya ke server lain secara berkala, agar backup tidak ikut hilang saat disk data rusak.

### Binary log

Binary log aktif (`scripts/binlog.cnf`, format `ROW`) dan mencatat setiap perubahan data.
`binlogbackup.sh` menutup binlog yang sedang aktif (`FLUSH BINARY LOGS`), lalu menyalin binlog yang sudah tertutup ke `/backup/binlog/`.
Jika tidak ada perubahan sejak run sebelumnya, binlog tidak ditutup, sehingga tidak muncul file kosong saat server sepi.

- Data yang bisa hilang paling lama sebesar interval `BINLOG_CRON` (default 15 menit). Binlog di data volume juga bisa dipakai jika volume itu masih utuh.
- Setelah full backup berhasil, arsip binlog sebelum posisi full backup tersebut dihapus, karena isinya sudah ada di full backup.
  Jadi `/backup/binlog` hanya berisi binlog sejak full backup terakhir. Jika full backup gagal, arsip tidak dihapus.
- Akibatnya, point-in-time recovery hanya bisa dimulai dari **full backup terakhir**. Full backup yang lebih lama tetap bisa di-restore, tetapi hanya ke waktu saat backup itu dibuat.
- Binlog di data volume dihapus otomatis setelah 2 hari. Arsip juga dihapus setelah 30 hari jika full backup dinonaktifkan (`BACKUP_CRON=off`).
- Bebannya kecil: server memang sudah menulis binlog, script hanya menyalin file yang sudah selesai dengan prioritas CPU/IO rendah.

Log ada di `/backup/log/` dan dihapus setelah 365 hari tidak berubah:

- `backup_log_<tahun>.log`, `dump_log_<tahun-bulan>.log`, dan `binlog_log_<tahun-bulan>.log`: jalannya proses backup.
- `database_backup_cron.log`, `database_dump_cron.log`, dan `binlog_backup_cron.log`: hanya error tak terduga dari job cron.

Script keluar dengan kode selain 0 jika backup gagal. Jika backup sebelumnya masih berjalan, script langsung berhenti agar tidak berjalan dobel.

Menjalankan backup secara manual:

```bash
docker exec <container> /opt/backup/databasebackup.sh
docker exec <container> /opt/backup/databasedump.sh
docker exec <container> /opt/backup/binlogbackup.sh
```

## Restore

### Dari dump (satu database)

Dump berisi `CREATE DATABASE`, tabel, data, trigger, routine, dan event. Database dengan nama yang sama akan ditimpa.

```bash
FILE=/backup/databases/<db>/<db>-<waktu>.sql.gz
docker exec <container> sh -c "gunzip -c $FILE" \
  | docker exec -i <container> sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
```

### User & grant

Database `mysql` tidak ikut di-dump, jadi user aplikasi dan hak aksesnya disimpan di dump terpisah.
Restore ini dulu sebelum restore database ke server baru. User yang sudah ada (mis. `root`) tidak ditimpa.

```bash
FILE=/backup/databases/_users/_users-<waktu>.sql.gz
docker exec <container> sh -c "gunzip -c $FILE" \
  | docker exec -i <container> sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
```

### Dari full backup (seluruh server)

Restore ke **volume baru** dulu, periksa hasilnya, baru ganti volume lama. Jangan langsung menimpa volume yang sedang dipakai.

```bash
FULL=<waktu>.xbstream.gz
docker volume create restore_data

docker run --rm --entrypoint bash \
  -v <volume_backup>:/backup:ro \
  -v restore_data:/var/lib/mysql \
  zmutclik/mariadb:12.3 -c "
    set -e
    mkdir -p /tmp/restore
    gunzip -c /backup/fullbackup/$FULL | mbstream -x -C /tmp/restore
    mariadb-backup --prepare --target-dir=/tmp/restore
    mariadb-backup --copy-back --target-dir=/tmp/restore
    chown -R mysql:mysql /var/lib/mysql
  "

# Periksa hasil restore di container sementara
docker run -d --name cek-restore -e MARIADB_AUTO_UPGRADE=1 \
  -v restore_data:/var/lib/mysql zmutclik/mariadb:12.3
docker exec -it cek-restore mariadb -uroot -p
```

Gunakan image dengan versi MariaDB yang sama dengan saat backup dibuat.
Password root dan user lain ikut dari backup, bukan dari `.env`.

Nama volume bisa dilihat dengan `docker volume ls` (biasanya `<nama_project>_data` dan `<nama_project>_backup`).

### Point-in-time recovery (sampai waktu tertentu)

Contoh: tabel terhapus tidak sengaja jam 14:32. Restore full backup terakhir sebelum kejadian, lalu putar ulang binlog sampai sesaat sebelumnya.

1. Lakukan [restore dari **full backup terakhir**](#dari-full-backup-seluruh-server) ke volume baru, tetapi sebelum `--prepare` catat posisi binlog saat backup dibuat:

   ```bash
   cat /tmp/restore/mariadb_backup_binlog_info
   # mysql-bin.000002	741	0-1-6   -> file, posisi, GTID
   ```

2. Jalankan container sementara (`cek-restore`) dengan volume backup ikut di-mount, lalu putar ulang binlog mulai dari posisi tersebut:

   ```bash
   docker run -d --name cek-restore -e MARIADB_AUTO_UPGRADE=1 \
     -v restore_data:/var/lib/mysql -v <volume_backup>:/backup:ro zmutclik/mariadb:12.3

   docker exec -it cek-restore bash
   mkdir -p /tmp/bl
   for f in /backup/binlog/mysql-bin.*.gz; do gunzip -c "$f" > /tmp/bl/$(basename "$f" .gz); done
   # Hanya file mulai dari file di mariadb_backup_binlog_info, urut sesuai nomor
   mariadb-binlog --start-position=741 --stop-datetime="2026-10-30 14:31:59" \
     /tmp/bl/mysql-bin.000002 /tmp/bl/mysql-bin.000003 ... \
     | mariadb -uroot -p
   ```

   `--start-position` hanya berlaku untuk file pertama. Untuk melihat isi binlog sebelum diputar ulang (mis. mencari waktu persis kejadian), gunakan `mariadb-binlog --verbose <file> | less`.

3. Periksa data, lalu ganti volume lama dengan volume hasil restore.

Waktu `--stop-datetime` memakai zona waktu container (Asia/Jakarta).
Jika data volume lama masih bisa dibaca, binlog terbaru (yang belum sempat diarsipkan) ada di `/var/lib/mysql/mysql-bin.*` volume tersebut.

## Perawatan

### Mengganti password backup

Ubah `BACKUP_PASS` di `.env`, lalu samakan di database dan buat ulang container:

```bash
docker exec -it <container> sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -e "ALTER USER '\''user_backup'\''@'\''localhost'\'' IDENTIFIED BY '\''<password_baru>'\''"'
docker compose up -d
```

### Upgrade versi MariaDB

Versi dikunci lewat `ARG MARIADB_VERSION` di `Dockerfile` (default `12.3`).
`MARIADB_AUTO_UPGRADE=1` menjalankan `mariadb-upgrade` otomatis saat container start dengan versi baru.
MariaDB tidak mendukung downgrade, jadi buat full backup sebelum upgrade.

### Build dan push image

```bash
docker login
./build-image.sh                        # zmutclik/mariadb:12.3 dan :latest
MARIADB_VERSION=11.8 ./build-image.sh   # versi lain
```

### TLS

`scripts/openssl.cnf` mengatur TLS minimal 1.2 dengan `CipherString = DEFAULT@SECLEVEL=1`.
`SECLEVEL=1` mengizinkan kunci dan cipher yang lebih lemah agar client lama tetap bisa terhubung.
Jika semua client sudah modern, ganti ke `SECLEVEL=2` untuk keamanan yang lebih baik.
