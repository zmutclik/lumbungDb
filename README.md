# LumbungDB

Pusat pengumpul backup dan laporan kesehatan untuk container MariaDB berbasis image `zmutclik/mariadb:lumbungdb-*`. Rancangan lengkap ada di `dokumen_pengembangan.md`.

| Folder | Isi |
| --- | --- |
| `server/` | LumbungDB: FastAPI, Jinja2, htmx, Alpine build CSP, MariaDB. Mengikuti skill `fullstack-architecture` |
| `mariadb/` | Image `zmutclik/mariadb:lumbungdb-*`: MariaDB, script backup, cron, dan agen |
| `deploy/` | Konfigurasi nginx |
| `config/` | `agents.conf`, satu-satunya tempat token agen |
| `scripts/` | Uji ujung-ke-ujung |
| `wireframe/` | Prototype dashboard dengan data contoh |

## Kunci enkripsi backup

Backup dienkripsi di agen sebelum dikirim. **Tanpa kunci privat, backup tidak bisa dipulihkan oleh siapa pun.** Buat dua pasang kunci di mesin tepercaya, bukan di server LumbungDB:

```bash
age-keygen -o age-runner.key      # disimpan di mesin runner
age-keygen -o age-pemulihan.key   # disimpan offline, sebaiknya di dua tempat fisik terpisah
```

Isi `BACKUP_AGE_RECIPIENTS` dengan kedua baris `# public key:` dari file itu, dipisah koma tanpa spasi. Kunci privat jangan pernah disalin ke server LumbungDB, NAS, atau server database. Sebelum data produksi masuk, latih satu pemulihan memakai kunci pemulihan.

## Memasang LumbungDB

```bash
cp .env.example .env && chmod 600 .env       # isi BACKUP_ROOT, DB_PASSWORD, SECRET_KEY, BACKUP_AGE_RECIPIENTS, Telegram
cp config/agents.conf.example config/agents.conf && chmod 600 config/agents.conf
touch /mnt/lumbungdb/.lumbung-mounted        # penanda mount, di NFS

docker compose run --rm web python -m app.auth.cli create-user admin # akun pertama; password dari prompt
docker compose up -d --build
```

SSL tidak dipasang di LumbungDB. Secara default (`NGINX_CONF=nginx-proxy.conf`) nginx hanya melayani HTTP di port `HTTP_PORT`, dan SSL dipasang di reverse proxy yang Anda kelola di depan VM ini. Proxy depan itu wajib:

- melayani HTTPS dengan sertifikat yang dipercaya umum, karena agen dan runner memverifikasinya;
- meneruskan Host asli dan `X-Forwarded-For` ke `http://<VM>:<HTTP_PORT>`;
- tidak membatasi ukuran body, dengan timeout minimal 1 jam untuk `/api/upload/`, `/api/runner/jobs/`, dan `/api/manual/files/`, karena file backup bisa puluhan GB.

Isi `TLS_PROXY_IP` di `.env` dengan IP proxy depan supaya rate limit login memakai IP asli user. Akses langsung ke `http://<VM>` tanpa proxy ditolak saat login, karena app menganggap browser selalu memakai https. Ingin SSL dipasang langsung di VM ini? Pakai `NGINX_CONF=nginx-tls.conf` dan letakkan `fullchain.pem` dan `privkey.pem` di `deploy/certs/`.

Skema database dibuat dan di-upgrade otomatis oleh service `migrate` sebelum `web` dan `worker` start, termasuk saat `docker compose run`. Bila skema di database lebih baru dari kode di image (misalnya setelah rollback image), `migrate` berhenti dengan pesan jelas dan `web`/`worker` tidak start; downgrade tidak pernah otomatis. Log-nya: `docker compose logs migrate`.

Folder `config/` harus bisa ditulis oleh `LUMBUNG_UID`, karena Tambah container menulis `agents.conf` lewat rename. Pasang pemeriksa `GET /api/watchdog` dari luar VM: 503 berarti pengecek alert berhenti.

## Build image MariaDB

```bash
cd mariadb
PUSH=0 MARIADB_VERSION=12.3 AGENT_VERSION=a1 ./build-image.sh   # zmutclik/mariadb:lumbungdb-12.3-a1
```

Image harus sudah ada di registry atau di server tujuan, karena perintah pasang menariknya lewat `docker compose`.

## Menambah container

Buka Tambah container di dashboard, isi nama dan port, lalu jalankan perintah `curl` yang tampil di server database. Script membuat `.env` dengan user dan password acak di subfolder `<project>/` pada folder tempat perintah dijalankan, menjalankan MariaDB, dan menunggunya sehat. Bila gagal, jalankan perintah yang sama lagi dari folder yang sama; user dan password tidak berubah. Perintah berlaku 2 jam (`INSTALL_COMMAND_HOURS`); yang baru bisa dibuat di halaman verifikasi.

Container lama yang volumenya harus tetap terpakai memakai bagian *Container lama atau pasang manual* di halaman yang sama. Bila `mariadb/docker-compose.lumbung.yml` berubah, salin juga ke `server/app/installs/files/`; test gagal bila keduanya berbeda.

## Menghapus container dari agents.conf

Memberi `#` di depan baris = container **Nonaktif**, tetap tampil beserta riwayatnya. Menghapus barisnya = container ikut dihapus dari daftar dalam satu putaran worker (sekitar satu menit), **hanya bila** agennya belum pernah mengirim heartbeat dan belum punya backup. Container yang sudah punya backup tetap tampil sebagai Nonaktif, karena riwayat dan folder NAS-nya masih ada.

## Merotasi token agen

Lakukan bila token sebuah container dicurigai bocor. Selama langkah 2 sampai 5, cron di container tetap membuat backup; file terkirim setelah token baru terpasang.

1. Buat token baru di server LumbungDB:
   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(24))'
   ```
2. Ganti token pada baris `nama = token` di `config/agents.conf`. Izin file tetap 600.
3. Restart LumbungDB, lalu pastikan `web` dan `worker` berjalan:
   ```bash
   docker compose restart web worker && docker compose ps
   ```
   Jangan lewati restart. Baris yang salah tulis menggagalkan start, sedangkan tanpa restart LumbungDB hanya mencatat error di log dan token lama tetap berlaku.
4. Pastikan token lama ditolak. Hasil yang benar adalah `401`:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'X-Token: <token-lama>' -H 'Content-Type: application/json' -d '{}' https://<alamat-lumbung>/api/heartbeat
   ```
5. Di server database, ganti `LUMBUNG_TOKEN` di `<project>/.env`, lalu buat ulang container di luar jam sibuk, karena MariaDB ikut restart:
   ```bash
   cd <project> && docker compose -p <project> up -d
   ```
   Pada pemasangan lama yang compose-nya masih bernama `docker-compose.lumbung.yml`, ganti namanya jadi `docker-compose.yml` dulu.
6. Pastikan container di dashboard kembali mengirim heartbeat, dan tinjau riwayat file serta tinjauan checksum sejak waktu token dicurigai bocor.

## Pengembangan

`./start.sh` menjalankan web dengan auto-reload dan worker di port 8037, memakai container `lumbungdev-db` dan NAS tiruan di `.dev/`. `./start.sh wireframe` menyajikan prototype di port 8038.

Untuk test suite:

```bash
docker run -d --name lumbungtest-db -p 127.0.0.1:33061:3306 \
  -e MARIADB_ROOT_PASSWORD=root -e MARIADB_DATABASE=lumbung_test \
  -e MARIADB_USER=app -e MARIADB_PASSWORD=app mariadb:11.4 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci

cd server
uv sync
TEST_DATABASE_URL="mysql+asyncmy://app:app@127.0.0.1:33061/lumbung_test" uv run pytest
uv run ruff check .
```

Test suite mengosongkan databasenya, jadi namanya wajib berakhiran `_test`.

`scripts/e2e.sh` menjalankan LumbungDB lewat nginx dan container MariaDB sungguhan, dengan NAS berupa folder lokal. Skrip ini hanya membuat dan menghapus project bernama `lumbunge2e*`.
