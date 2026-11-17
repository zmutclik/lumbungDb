# Runner LumbungDB

Program terpisah yang mengerjakan uji restore. Ia mengambil tugas dari LumbungDB, mengunduh file lewat HTTPS,
memulihkannya di container sementara tanpa jaringan, lalu melaporkan hasil. Data hasil restore tidak keluar dari mesin ini.

- Boleh dipasang di server yang sama dengan container yang diuji: container uji tanpa jaringan dan dibuang sesudahnya.
  Database cadangan di server yang sama ikut hilang bila server itu mati, jadi untuk cadangan lebih baik runner di server lain.
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
`.env` dan `docker-compose.yml` di `lumbung-runner/` pada folder tempat perintah dijalankan, lalu menjalankan runner dengan project
`lumbung-runner`. Perintah berlaku `INSTALL_COMMAND_HOURS`, terikat ke mesin pertama yang memakainya, dan mati setelah
runner mengirim heartbeat; perintah baru ada di menu ⋮ runner yang belum terhubung. Bila
`docker-compose.runner.yml` berubah, salin juga ke `server/app/runner_installs/files/`; test gagal bila keduanya berbeda.

Pasang manual: di satu folder di mesin runner, salin `.env.runner.example` jadi `.env` dan `docker-compose.runner.yml` jadi
`docker-compose.yml`, isi `.env`, lalu `docker compose up -d`. Pemasangan lama yang masih memakai `.env.runner` dipindah
ke `.env` saat script pasang dijalankan ulang.

## Update

1. Rilis: `RUNNER_VERSION=r2 ./build-image.sh` (image `zmutclik/lumbungdb-runner:r2`), lalu naikkan `RUNNER_VERSION_LATEST=r2`
   di `.env` LumbungDB. Runner yang masih lebih lama ditandai **Perlu update ke r2** di halaman Runner.
2. Di mesin runner: ganti `RUNNER_IMAGE` di `.env` (folder `lumbung-runner/`), lalu `docker compose up -d`.

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

## Container yang dikelola runner

Di **Tambah container**, pilih *Dipasang di: Mesin runner …* supaya MariaDB langsung dipasang di mesin runner itu tanpa
menjalankan perintah apa pun. Runner (r3 ke atas) mengambil tugasnya lewat jalur kontrol yang terpisah dari uji restore,
jadi tidak menunggu uji yang sedang berjalan:

- `docker pull zmutclik/mariadb:lumbungdb-<tag>` (tag dipilih dari `IMAGE_TAGS` di `.env` LumbungDB), lalu `docker run` dengan `--restart always`, batas 2 CPU dan
  2g memori, port pilihan admin, volume `<project>_data` dan `<project>_backup`, serta label `lumbung.managed=1`.
- User aplikasi, user backup, dan semua password dibuat acak di runner dan disimpan di
  `<SCRATCH_DIR>/containers/<nama>/.env` (izin 600). LumbungDB tidak menyimpannya; buka file itu untuk password aplikasi.
- Ditolak bila container atau volume bernama sama sudah ada. Bila `docker run` gagal (mis. port terpakai), container,
  volume baru, dan `.env`-nya dibuang supaya bisa **Pasang ulang** dari halaman verifikasi.

Container yang dipasang lewat runner atau hasil **Naikkan ke production** bisa di-**Start**, **Stop**, dan **Restart**
dari menu ⋮ di detail container. Runner hanya menyentuh container berlabel `lumbung.managed=1` atau `lumbung.promoted=1`.

**Update image** (menu yang sama) memasang tag `zmutclik/mariadb:lumbungdb-<tag>` lain, dipilih dari `IMAGE_TAGS`. Runner menarik image dulu
(bila gagal, container tidak disentuh), mematikan restart dan menghentikan container lama, mengganti namanya menjadi
`<nama>-lama-<tugas>`, lalu menjalankan container baru dengan volume, bind, port, label `lumbung.*`, batas CPU/memori,
healthcheck, argumen MariaDB, dan `.env` yang sama (`<SCRATCH_DIR>/containers/<nama>/.env`, atau
`PROMOTE_DIR/<nama>/.env` untuk hasil penaikan). ENV dari inspect tidak disalin, karena berisi ENV image lama. Bila
yang baru tidak sehat, ia dibuang (tanpa volume) dan yang lama dijalankan lagi. LumbungDB menolak versi MariaDB yang
turun. Versi MariaDB yang naik menjalankan `mariadb-upgrade`; bila gagal di tengah, container lama mungkin tidak bisa
membaca datanya lagi, jadi pastikan ada full backup yang lolos uji.

**Kelola lewat runner** (menu ⋮ container yang belum dikelola runner) mengambil alih container yang dipasang di luar
runner, mis. lewat perintah curl dan `docker compose`. Syaratnya runner r4 berjalan di mesin yang sama (machine-id agen
dan runner sama) dan image-nya `zmutclik/mariadb:lumbungdb-*` (mode sidecar belum bisa). Runner menyusun `.env` di
`<SCRATCH_DIR>/containers/<nama>/.env` dari environment container dikurangi ENV bawaan image-nya, lalu membuat ulang
container dengan cara yang sama seperti Update image: nama, image yang persis sama (ID image, bila tag-nya sudah
menunjuk image lain), volume, port, batas CPU/memori, dan healthcheck, ditambah label `lumbung.managed=1`. Label
compose tidak disalin, jadi `docker compose` di folder lama tidak lagi memegangnya: `up` ditolak karena nama bentrok.
Pensiunkan folder itu. Container hasil Naikkan ke production sebelum fitur ini cukup dicatat tanpa dibuat ulang.

Heartbeat runner juga membawa CPU (rata-rata sejak heartbeat sebelumnya), load, dan memori seluruh mesin dari `/proc`
(di VM atau komputer tanpa lxcfs, angka ini milik host, bukan container runner), serta status container yang dikelolanya.
Semua tampil di halaman Runner.

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
