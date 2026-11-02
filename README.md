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

## Memasang LumbungDB

```bash
cp .env.example .env && chmod 600 .env       # isi BACKUP_ROOT, DB_PASSWORD, SECRET_KEY, Telegram
cp config/agents.conf.example config/agents.conf && chmod 600 config/agents.conf
touch /mnt/lumbungdb/.lumbung-mounted        # penanda mount, di NFS
mkdir -p deploy/certs                        # fullchain.pem dan privkey.pem

docker compose up -d db
docker compose run --rm web alembic upgrade head                     # sekali per deploy, sebelum up
docker compose run --rm web python -m app.auth.cli create-user admin # akun pertama; password dari prompt
docker compose up -d --build
```

Folder `config/` harus bisa ditulis oleh `LUMBUNG_UID`, karena Tambah container menulis `agents.conf` lewat rename. Pasang pemeriksa `GET /api/watchdog` dari luar VM: 503 berarti pengecek alert berhenti.

## Build image MariaDB

```bash
cd mariadb
PUSH=0 MARIADB_VERSION=12.3 AGENT_VERSION=a1 ./build-image.sh   # zmutclik/mariadb:lumbungdb-12.3-a1
```

Image harus sudah ada di registry atau di server tujuan, karena perintah pasang menariknya lewat `docker compose`.

## Menambah container

Buka Tambah container di dashboard, isi nama dan port, lalu jalankan perintah `curl` yang tampil di server database. Script membuat `.env` dengan password acak di subfolder `<project>/` pada folder tempat perintah dijalankan, menjalankan MariaDB, dan menunggunya sehat. Bila gagal, jalankan perintah yang sama lagi dari folder yang sama; password tidak berubah. Perintah berlaku 2 jam (`INSTALL_COMMAND_HOURS`); yang baru bisa dibuat di halaman verifikasi.

Container lama yang volumenya harus tetap terpakai memakai bagian *Container lama atau pasang manual* di halaman yang sama. Bila `mariadb/docker-compose.lumbung.yml` berubah, salin juga ke `server/app/installs/files/`; test gagal bila keduanya berbeda.

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
