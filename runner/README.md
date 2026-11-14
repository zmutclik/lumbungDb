# Runner LumbungDB

Program terpisah yang mengerjakan uji restore. Ia mengambil tugas dari LumbungDB, mengunduh file lewat HTTPS,
memulihkannya di container sementara tanpa jaringan, lalu melaporkan hasil. Data hasil restore tidak keluar dari mesin ini.

- Uji restore boleh di server yang sama dengan container yang diuji: container ujinya tanpa jaringan dan dibuang sesudahnya.
  Database cadangan dan Naikkan ke production tetap ditolak bila `machine-id` runner sama dengan container sumbernya.
- Mesin hanya butuh Docker dan akses HTTPS ke LumbungDB. `age`, `mbstream`, dan `mariadb-backup` dijalankan dari image MariaDB.
- Hasil: **Lolos**, **Gagal** (backup-nya yang bermasalah: file rusak, prepare gagal, tabel tidak terbaca), atau
  **Tidak terlaksana** (runner yang bermasalah: unduhan putus, Docker error, scratch penuh). Yang terakhir tidak dihitung gagal.
- Unduhan dilanjutkan dari byte terakhir bila terputus. Saat start, runner membuang container berlabel
  `lumbung.runner=1` dan folder `job-*` sisa tugas yang terputus.

Pasang: buka **Tambah runner** di halaman Runner, lalu jalankan perintah yang tampil di mesin runner:

```bash
curl -fsSL https://<lumbung>/install-runner.sh | LUMBUNG_URL=https://<lumbung> INSTALL_TOKEN=lmn_xxx sh
```

Kunci privat age runner harus sudah ada di `/etc/lumbung/age-runner.key` (izin 600; lokasi lain lewat
`AGE_IDENTITY_FILE=/path` sebelum `sh`). Script memeriksa kunci itu salah satu penerima `BACKUP_AGE_RECIPIENTS`, menulis
`.env.runner` di `lumbung-runner/` pada folder tempat perintah dijalankan, lalu menjalankan runner dengan project
`lumbung-runner`. Perintah berlaku `INSTALL_COMMAND_HOURS`, terikat ke mesin pertama yang memakainya, dan mati setelah
runner mengirim heartbeat; perintah baru ada di menu ⋮ runner yang belum terhubung. Bila
`docker-compose.runner.yml` berubah, salin juga ke `server/app/runner_installs/files/`; test gagal bila keduanya berbeda.

Pasang manual: salin `.env.runner.example` jadi `.env.runner`, isi, lalu `docker compose -f docker-compose.runner.yml --env-file .env.runner up -d`.

## Update

1. Rilis: `RUNNER_VERSION=r2 ./build-image.sh` (image `zmutclik/lumbungdb-runner:r2`), lalu naikkan `RUNNER_VERSION_LATEST=r2`
   di `.env` LumbungDB. Runner yang masih lebih lama ditandai **Perlu update ke r2** di halaman Runner.
2. Di mesin runner: ganti `RUNNER_IMAGE` di `.env.runner`, lalu
   `docker compose -f docker-compose.runner.yml --env-file .env.runner up -d`.

Bisa kapan saja; database cadangan dan container hasil promote adalah container sendiri dan tetap hidup. Saat `docker stop`:

- Uji restore dan unduhan langsung dihentikan. LumbungDB menandai tugasnya tidak selesai setelah batas waktu tugas
  lewat; uji berikutnya berjalan sesuai jadwal. Sisa container dan folder dibersihkan saat runner start.
- Penerapan binlog juga langsung dihentikan. Database cadangan tetap hidup, tetapi penerapan berikutnya ditolak sampai
  pemulihan full berikutnya (atau **Restore sekarang**).
- Mengganti database cadangan dan naikkan ke production ditunggu sampai selesai (`stop_grace_period: 15m`), karena
  terputus di tengah langkah itu bisa meninggalkan database cadangan tanpa akun runner atau datadir setengah pindah.

Paling rapi: update saat kolom **Tugas berjalan** di halaman Runner kosong. Kembali ke versi lama cukup dengan tag lama.

Uji sungguhan terhadap Docker tanpa LumbungDB: `python3 scripts/sim/runner_real.py <image>` (tambah `--corrupt` untuk
memastikan full yang rusak terdeteksi Gagal).

## Naikkan ke production

Tugas `standby_promote` mengubah database cadangan di mesin ini menjadi container production bernama baru:
user backup baru dibuat (password acak), akun `lumbung_runner` dihapus, datanya dipindah ke
`PROMOTE_DIR/<nama>/data` (bawaan `<SCRATCH_DIR>/promoted`), lalu container dijalankan dengan `--restart always`, `.env`
berizin 600 di `PROMOTE_DIR/<nama>/.env`, batas `PROMOTE_MEMORY` dan `PROMOTE_CPUS` (bawaan 2g dan 2), serta port yang
dipilih admin. Bila ada langkah yang gagal sebelum container terbukti siap tulis tanpa akun runner, datanya dikembalikan,
akun runner dibuat lagi, dan database cadangan hidup seperti semula.

Data production kemudian berada di disk scratch mesin ini. Pakai `PROMOTE_DIR` di disk yang sama kualitasnya dengan data
production (terenkripsi, dengan cadangan), dan pastikan `PROMOTE_DIR` satu filesystem dengan `SCRATCH_DIR` supaya
pemindahan datanya berupa rename, bukan salinan.
