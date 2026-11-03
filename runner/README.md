# Runner LumbungDB

Program terpisah yang mengerjakan uji restore. Ia mengambil tugas dari LumbungDB, mengunduh file lewat HTTPS,
memulihkannya di container sementara tanpa jaringan, lalu melaporkan hasil. Data hasil restore tidak keluar dari mesin ini.

- Pasang di server yang berbeda dari server container yang diuji. LumbungDB menolak tugas bila `machine-id` sama.
- Mesin hanya butuh Docker dan akses HTTPS ke LumbungDB. `age`, `mbstream`, dan `mariadb-backup` dijalankan dari image MariaDB.
- Hasil: **Lolos**, **Gagal** (backup-nya yang bermasalah: file rusak, prepare gagal, tabel tidak terbaca), atau
  **Tidak terlaksana** (runner yang bermasalah: unduhan putus, Docker error, scratch penuh). Yang terakhir tidak dihitung gagal.
- Unduhan dilanjutkan dari byte terakhir bila terputus. Saat start, runner membuang container berlabel
  `lumbung.runner=1` dan folder `job-*` sisa tugas yang terputus.

Pasang: salin `.env.runner.example` jadi `.env.runner`, isi, lalu `docker compose -f docker-compose.runner.yml --env-file .env.runner up -d`.

Uji sungguhan terhadap Docker tanpa LumbungDB: `python3 scripts/sim/runner_real.py <image>` (tambah `--corrupt` untuk
memastikan full yang rusak terdeteksi Gagal).
