# LumbungDB — Dokumen Pengembangan

Oct 30, 2026 · @Fahrudin Hariadi

## Ringkasan

LumbungDB adalah pusat pengumpul backup dan laporan kesehatan untuk container MariaDB berbasis image `zmutclik/mariadb:lumbungdb-*`: tiap container mengirim hasil backup ke satu pusat, yang menyimpannya di NAS Synology lewat NFS, memberi peringatan bila ada yang tidak beres, dan membuktikan backup itu bisa dipulihkan.

Masalah yang dijawab: image MariaDB menulis backup ke volume `/backup` di server yang sama dengan datanya, sehingga backup bisa ikut hilang bersama disk server, dan kegagalan backup baru diketahui saat backup dibutuhkan. LumbungDB memindahkan salinan ke tempat terpisah dan memantau bahwa backup benar-benar datang.

Status per 3 November 2026:

- Kode tahap 1 (pengumpulan backup, dashboard status, alert Telegram, image, dan kendali backup) sudah ada. Uji otomatisnya lolos pada 3 November 2026: 203 tes pytest, dan uji ujung-ke-ujung `scripts/e2e.sh` lolos dua kali berturut-turut (144 pemeriksaan) dengan container MariaDB asli, nginx, enkripsi `age`, dan pemulihan sungguhan dari file terenkripsi.
- Uji ujung-ke-ujung itu memakai folder lokal sebagai NAS dan Telegram tiruan. Belum ada yang diuji dengan NFS Synology sungguhan, Telegram sungguhan, atau data produksi.
- Audit keamanan tahap 1 belum selesai: kandidat temuannya belum divalidasi, dan audit dilanjutkan setelah semua kode selesai.
- Kode tahap 3 (metrik dari agen, tabel `metrics`, laporan harian dan mingguan, alert perkiraan disk) sudah ada dan lolos uji otomatis dengan data contoh pada 3 November 2026. Belum ada database nyata yang mengirim metrik tujuh hari.
- Tahap 2 sebagian selesai (3 November 2026): program runner (`runner/`), penjadwalan uji restore, dan alert runner dan uji restore sudah ada. Uji restore full, dump, `_users`, dan binlog terbukti dengan Docker sungguhan lewat `scripts/sim/runner_real.py`, termasuk full yang dirusak terdeteksi Gagal. Dashboard: menu Runner (status, heartbeat, scratch, tugas berjalan, riwayat tugas, Tambah runner dengan token sekali tampil, Nonaktifkan) dan tab Uji restore di detail container (runner, jadwal, cakupan, Uji sekarang, riwayat per jenis). Retensi di NAS berjalan di worker tiap jam: full dan dump disimpan 30 hari lalu satu per bulan sampai 12 bulan, binlog 3 hari, dengan perlindungan untuk yang terbaru, bukti uji, rantai binlog full yang lolos uji, tugas yang belum selesai, dan versi yang belum ditinjau; tiap file yang dihapus tercatat di `retention_log`. Database cadangan terjadwal berjalan: runner memulihkan full + binlog ke volume baru, membuat akun `lumbung_runner`, memeriksa, lalu mengganti yang lama (yang lama dihidupkan lagi bila gagal); binlog baru diterapkan sesuai jadwal; penerapan yang gagal atau terputus menghentikan penerapan sampai full berikutnya. Berjalan `read_only`, event scheduler mati, port terikat ke `STANDBY_BIND_ADDRESS` (bawaan 127.0.0.1). Terbukti dengan Docker sungguhan (`scripts/sim/standby_real.py`): isi cocok dengan sumber setelah full+binlog, setelah penerapan binlog baru, dan setelah pemulihan ulang. Tab Cadangan dan Restore sekarang (termasuk pindah runner) ada di dashboard. Belum ada: penghapusan salinan di runner lama saat runner dipindah atau jadwal dimatikan, Naikkan ke production, Restore manual, pindai ulang NAS, dan antrean notifikasi. Tahap 4 (lapisan AI) masih rancangan, lihat bagian Roadmap.
- Keputusan 3 November 2026: retensi di NAS harian 30 hari, bulanan 12 bulan, dan binlog 3 hari. Backup dienkripsi di agen sebelum dikirim, lihat Enkripsi backup di bagian Keamanan.
- Keputusan 3 November 2026: aplikasi baru dipakai setelah lengkap, jadi tidak ada data produksi yang masuk di antara tahap. Tahap 2 sampai 4 dibangun dengan data contoh, dan semua gerbang menjadi uji penerimaan sebelum pemakaian pertama. Lihat Roadmap.
- Perubahan rancangan 1 November 2026: file bernama sama dengan isi berbeda kini disimpan sebagai versi baru. Perubahan ini termasuk tahap 1 dan wajib selesai sebelum dipakai untuk data produksi.
- Perubahan rancangan 1 November 2026: umur backup dihitung dari waktu pembuatan, bukan waktu diterima, dan alert dump dinilai per database dengan aturan sendiri untuk `_users`. Perubahan ini juga termasuk tahap 1 dan wajib selesai sebelum produksi.
- Perubahan rancangan 1 November 2026: pemeriksaan NAS dipisahkan ke proses kecil dengan batas waktu, supaya NFS yang macet tidak menghentikan alert. Perubahan ini juga termasuk tahap 1 dan wajib selesai sebelum produksi.
- Perubahan rancangan 1 November 2026: istilah "server" diganti Server dan Container, karena satu server bisa menjalankan banyak container MariaDB. Perubahan ini termasuk tahap 1.
- Perubahan rancangan 1 November 2026: uji restore dan database cadangan dikerjakan runner, program terpisah yang mengunduh file lewat LumbungDB dan tidak punya akses NFS. Uji restore dan database cadangan punya jadwal sendiri per container. Restore lewat LumbungDB memulihkan seluruh container (full dan binlog) di runner cadangan. Semua ini termasuk tahap 2. Prototype sudah menampilkannya; kodenya belum ada.
- Perubahan rancangan 1 November 2026: tiap container punya runner uji dan runner cadangan yang bisa berbeda. Perubahan ini termasuk tahap 2. Prototype sudah menampilkannya; kodenya belum ada.
- Perubahan rancangan 1 November 2026: agen pindah ke dalam image `lumbungdb/mariadb` milik LumbungDB, yang menggantikan `zmutclik/mariadb`, dan sidecar tidak dipakai lagi. Semua container memakai image ini, jadi jadwal semuanya bisa diubah dari dashboard, dan admin bisa meminta backup sewaktu-waktu. Cron di container tetap yang menjalankan backup, jadi backup tetap berjalan saat LumbungDB mati. Perubahan ini termasuk tahap 1 dan wajib selesai sebelum produksi. Prototype sudah menampilkannya; kodenya belum ada.
- Perubahan rancangan 2 November 2026: Siapkan restore dihapus. Restore lewat LumbungDB untuk sekarang hanya seluruh container di runner cadangan; menu restore manual menyusul. Hasil restore yang tetap hidup di mesin runner disebut database cadangan; prosesnya disebut restore, misalnya Restore sekarang.
- Perubahan rancangan 2 November 2026: Pakai cadangan, kunci, dan Lepas kunci diganti Naikkan ke production. Larangan dinilai per container: uji dan cadangan tidak berjalan di server yang sama dengan container itu. Restore sekarang bisa memilih runner lain.
- Perubahan rancangan 2 November 2026: kata "host" tidak dipakai lagi. Mesin disebut server, dan menu Server menjadi menu Container.
- Perubahan rancangan 2 November 2026: variabel nama mesin di `.env` dihapus. Agen dan runner membaca identitas server dari mesinnya dan mengirimnya lewat heartbeat, jadi nama server tidak lagi diisi manual.
- Perubahan rancangan 2 November 2026: nama container dan runner harus diawali huruf atau angka, dan dua container tidak boleh menghasilkan nama project compose yang sama. Tag image diisi lewat `IMAGE_TAG` di `.env`, tanpa nilai bawaan di compose. Naikkan ke production ditolak bila `agents.conf` gagal ditulis, dan container hasilnya yang belum punya runner di server lain memicu alert.
- Perubahan rancangan 2 November 2026: image dipublikasikan sebagai `zmutclik/mariadb:lumbungdb-<versi>`, bukan `lumbungdb/mariadb`, karena organisasi `lumbungdb` tidak ada di Docker Hub. Nilai `IMAGE_TAG` tetap `12.3-a1`; awalan `lumbungdb-` ada di compose.
- Perubahan rancangan 2 November 2026: Tambah container memakai satu perintah `curl` seperti Restore manual, supaya admin tidak perlu menyalin `.env` dan compose. Script membuat password di server; LumbungDB tetap tidak menyimpannya. Salin manual tetap ada untuk container lama.
- Perubahan rancangan 2 November 2026: tab Restore manual ditambahkan di detail container untuk pemulihan tanpa runner. Admin memilih full, menjalankan satu perintah `curl` di server tujuan dengan token berbatas waktu, dan hasil yang lolos pemeriksaan langsung terdaftar sebagai container baru. Perubahan ini termasuk tahap 2. Prototype sudah menampilkannya; kodenya belum ada.
- Perubahan rancangan 2 November 2026: agen berjalan sebagai user non-root `lumbung`, dan penerapan jadwal serta pemanggilan script lewat `lumbung-helper` milik root. Perubahan ini termasuk tahap 1 dan wajib selesai sebelum produksi.
- Perubahan rancangan 2 November 2026: arsip binlog yang belum terkirim tidak dihapus setelah full, tetapi hanya ditahan selama `BINLOG_UNSENT_MAX_DAYS` (bawaan 3 hari). Daftar perubahan image dilengkapi: `install_cron.sh`, `hostname`, Dockerfile, dan entrypoint. `CONTAINER_HOSTNAME` di `.env` berisi nama container, bukan nama mesin; `HOSTNAME` tidak dipakai, karena nilai dari shell server mengalahkan `.env`. Perubahan ini termasuk tahap 1.
- Perubahan rancangan 2 November 2026: tabel `containers` dan kolom `backups.request_id` ditambahkan. Perubahan ini termasuk tahap 1.
- Perubahan rancangan 2 November 2026: binlog kini dikelompokkan per rantai lewat `chain_id`, dan tiap full mencatat `binlog_start`. Pencatatannya termasuk tahap 1, supaya data dari backup pertama sudah lengkap; pemakaiannya di tahap 2.
- Perubahan rancangan 2 November 2026: runner memakai akun sementara `lumbung_runner` di hasil restore. Naikkan ke production membuat user backup baru. Database cadangan berjalan tanpa event scheduler. Perubahan ini termasuk tahap 2.
- Perubahan rancangan 2 November 2026: Basic auth dengan satu akun diganti akun admin per orang dengan sesi login. Semua akun punya hak yang sama. Perubahan ini termasuk tahap 1 dan wajib selesai sebelum produksi. Prototype belum menampilkan halaman akun admin; kodenya belum ada.
- Perubahan rancangan 2 November 2026: LumbungDB berjalan di balik reverse proxy di jaringan lokal, tanpa akses dari internet, memakai SSL beli yang hanya dipasang di proxy. `TRUSTED_PROXY_IPS` menentukan dari mana header `X-Forwarded-*` dipercaya. Perubahan ini termasuk tahap 1 dan wajib selesai sebelum produksi.

## Istilah

| Istilah | Arti |
| --- | --- |
| LumbungDB | Program pusat: menerima backup, menyimpannya di NAS, menilai alert, dan melayani dashboard |
| Server | Mesin fisik atau VM yang menjalankan container MariaDB |
| Container | Satu MariaDB yang berjalan di server. Satu container memiliki satu agen dan satu token |
| Database | Isi container yang di-dump per database |
| Agen | Program kecil di dalam image MariaDB yang mengirim backup dan heartbeat, serta menerapkan jadwal dan permintaan backup |
| Image MariaDB | `zmutclik/mariadb:lumbungdb-*`: MariaDB, script backup, cron, dan agen. Source-nya bagian dari project LumbungDB |
| Permintaan backup | Backup satu kali di luar jadwal yang diminta admin dari dashboard |
| Akun admin | Akun login dashboard milik satu orang. Semua akun punya hak yang sama |
| Helper | `lumbung-helper`: script kecil milik root di dalam image, satu-satunya jalur agen untuk menerapkan jadwal dan menjalankan script backup |
| Runner | Server atau VM khusus yang menjalankan uji restore dan database cadangan, lewat program runner |
| Database cadangan | Hasil pemulihan full dan binlog yang tetap hidup di mesin runner, untuk kejadian tidak terduga |
| Restore manual | Pemulihan seluruh container ke server mana pun lewat satu perintah, tanpa runner. Hasilnya terdaftar sebagai container baru |
| Sesi restore | Satu Restore manual: full yang dipilih, nama container baru, token, dan batas waktunya |

## Ruang lingkup

LumbungDB mengumpulkan, memantau, menguji, dan mengatur jadwal backup; ia tidak me-restore ke produksi. Pembuatan backup tetap dikerjakan script dan cron di dalam image `zmutclik/mariadb:lumbungdb-*`.

Di dalam lingkup:

- Mengirim file backup dari tiap container ke LumbungDB.
- Menyimpan file di NAS Synology lewat NFS, satu folder per container.
- Menampilkan status backup terakhir dan kesehatan agen di dashboard.
- Mengirim peringatan Telegram bila backup tidak masuk, agen mati, atau ruang NAS menipis.
- Image MariaDB sebagai referensi pemasangan, jadwal backup yang bisa diubah dari dashboard, dan permintaan backup sewaktu-waktu.
- Uji restore terjadwal, database cadangan terjadwal, Restore sekarang, dan Restore manual (tahap 2).
- Laporan kesehatan database periodik dan ringkasan AI (tahap 3 dan 4).

Sengaja di luar lingkup:

- Menjalankan perintah bebas di container. Agen hanya mengenal jadwal dan tiga jenis backup, bukan perintah shell.
- Container MariaDB dengan image lain. Semua container harus memakai tag `lumbungdb-*` dari `zmutclik/mariadb`, termasuk yang sekarang memakai tag lama seperti `zmutclik/mariadb:12.3`.
- Restore otomatis ke database produksi. Uji restore hanya ke container sementara, database cadangan hanya di mesin runner, dan pemulihan di luar itu dijalankan admin secara manual ke volume baru.
- Memindahkan aplikasi ke database cadangan. Itu tetap keputusan manusia.
- Agen yang mengelola Docker di server produksi, karena akses Docker socket setara root.
- Menggantikan monitoring umum seperti Prometheus atau Grafana.
- Database selain MariaDB.

## Arsitektur

Agen di dalam tiap container mendorong file backup ke LumbungDB, yang menulisnya ke NAS lewat NFS dan menilai alert. Runner mengambil tugas dan file dari LumbungDB.

```text
Server database                                 VM LumbungDB                NAS Synology
  MariaDB simrs-utama + agen  --HTTPS-->      LumbungDB        --NFS-->   /lumbungdb
  MariaDB siak-db     + agen  --HTTPS-->      (hanya ini yang punya NFS)

VM atau server khusus
  Runner  --HTTPS: ambil tugas, unduh file, kirim hasil-->  LumbungDB
    |- container sementara uji restore
    `- database cadangan
```

Koneksi satu arah: agen dan runner selalu yang menghubungi LumbungDB; LumbungDB tidak pernah menarik dari server database dan tidak pernah masuk ke mesin runner. Agen mengirim file dari `/backup`, dan mengambil perubahan jadwal serta permintaan backup dari jawaban heartbeat, tanpa port baru. Pengecek alert berjalan sebagai proses worker sendiri: ia membaca metadata di MariaDB, dan memeriksa mount serta ruang NAS lewat proses kecil terpisah. Tiap container memiliki agen yang sama dengan tokennya sendiri.

Keputusan desain dan alasannya:

- **Dorong (push), bukan tarik.** Tiap container punya jadwal backup sendiri, dan tidak perlu membuka akses SSH ke tiap container.
- **LumbungDB menulis ke NFS yang di-mount di server,** bukan di dalam container, dengan file penanda untuk mendeteksi mount yang hilang.
- **Pemantau dipisahkan dari jalur file.** Mount `hard` membuat proses yang membaca NFS menunggu tanpa batas saat NAS macet, jadi yang menyentuh NAS untuk pemantauan hanya proses kecil yang boleh tersangkut.
- **Metadata di MariaDB sendiri.** Satu container `db` di VM LumbungDB, terpisah dari database yang dipantau, supaya web berbeda proses dan worker berbagi status tanpa menyimpannya di memori.
- **Alert dihitung dengan aturan biasa,** bukan AI, agar kegagalan backup tidak bergantung pada tebakan model.
- **Runner terpisah dari LumbungDB.** Runner memegang akses Docker di mesinnya, yang setara root. LumbungDB menerima koneksi dari jaringan, jadi tidak boleh memegang Docker socket.
- **Runner tanpa akses NFS.** Runner hanya bisa mengunduh file dari tugasnya, dan tiap unduhan tercatat. Bila runner dibobol, penyerang tidak langsung bisa membaca backup semua container.
- **Otak tetap di satu tempat.** Jadwal, pembagian tugas, dan riwayat ada di LumbungDB; runner hanya pekerja. Tugas yang tidak diambil langsung terlihat dan memicu alert.
- **Tidak ada agen pengelola Docker di server produksi.** Lokasi server, sisa disk, dan status agen cukup diketahui dari heartbeat.
- **Agen di dalam image, bukan sidecar.** Sidecar dengan `/backup` read-only tidak bisa menjalankan script backup tanpa Docker socket, yang setara root di server.
- **Jadwal diatur LumbungDB, dijalankan cron setempat.** Jadwal terakhir tersimpan di container, jadi backup tidak bergantung pada LumbungDB yang hidup.

## Komponen

Ada tiga program: LumbungDB yang menerima dan memantau, agen kecil di dalam tiap container yang mengirim, dan runner yang menguji serta memulihkan.

### LumbungDB (`server/app/main.py`)

FastAPI, Jinja2, htmx, dan Alpine build CSP, dengan metadata di MariaDB lewat SQLAlchemy async dan Alembic, dan file backup di `/data` (mount NFS dari server). Ada dua proses: `web` melayani API dan dashboard, dan `worker` (`app/alerts/worker.py`, satu instance) memeriksa NAS dan menilai alert.

- Upload di-stream ke file `.part`, di-fsync, dicek SHA256, lalu di-rename ke nama akhir. Tidak pernah ada file setengah jadi dengan nama final.
- Tiap 60 detik pengecek alert menjalankan proses kecil terpisah yang membaca `.lumbung-mounted` dan sisa ruang NAS, lalu menunggunya paling lama `MOUNT_PROBE_TIMEOUT_SECONDS`. Bila lewat batas, NAS dianggap tidak merespons dan pengecek tetap berjalan. Selama proses itu masih tersangkut, tidak dibuat proses baru; status pulih saat proses itu selesai dengan hasil baik.
- Hasilnya disimpan di tabel `nas_status`. Upload, dashboard, dan `/api/watchdog` membaca baris ini, bukan NAS. Status yang lebih tua dari `NAS_STATUS_STALE_SECONDS` berarti pengecek berhenti: upload ditolak, dan dashboard memberi banner.
- Upload ditolak (HTTP 503) bila status NAS menunjukkan file penanda `.lumbung-mounted` tidak ada atau NAS tidak merespons, supaya tidak menulis ke disk lokal saat NFS belum ter-mount dan tidak ikut tersangkut.
- Upload bersamaan dibatasi `UPLOAD_MAX_CONCURRENT`, yang dibagi rata ke tiap proses web; kelebihannya ditolak 503 dan diulang agen, supaya upload yang tersangkut tidak menghabiskan worker dashboard.
- Upload file bernama sama dibandingkan lewat checksum. Bila sama dengan versi mana pun yang tersimpan, dijawab `exists` dan tidak ada yang disimpan, jadi aman diulang. Bila berbeda, file disimpan sebagai versi baru `<nama>.v<N>-<8 karakter awal SHA256>` dan dijawab `version`. Versi baru dicatat di `integrity_events` untuk ditinjau, kecuali binlog yang masuk rantai berbeda, karena itu kejadian normal setelah restore. File yang sudah ada tidak pernah ditimpa.
- Arsip binlog bernama `<nama>.<waktu>.gz`, yang dibuat `binlogbackup.sh` saat nomor bentrok, hanya nama lokal di container. LumbungDB mengembalikannya ke `<nama>.gz` sebelum membandingkan checksum, jadi isinya yang sudah tersimpan dijawab `exists`, dan di NAS hanya ada satu skema versi.
- LumbungDB menyetel waktu ubah file di NAS sama dengan `backup_at`, supaya pemindaian ulang bisa menghitung rantai kembali.
- Binlog untuk sebuah full dipilih menurut rantainya (lihat Model data). Bila satu rantai, atau satu nama full atau dump, punya dua isi berbeda, yang dipakai adalah versi yang paling akhir diterima. Versi yang belum ditinjau tidak dihapus retensi.
- Pengecekan alert berjalan tiap 60 detik di latar belakang. Dashboard menyegarkan diri tiap 60 detik. Penyegaran hanya memperbarui data, tanpa memuat ulang halaman penuh, dan dijeda di Tambah container dan Tambah runner, serta saat dialog atau form terbuka, supaya token yang tampil sekali tidak hilang.

| Variabel | Default | Fungsi |
| --- | --- | --- |
| `AGENTS_FILE` | `/config/agents.conf` | Lokasi file daftar agen (lihat di bawah) |
| `DATA_DIR` | `/data` | Lokasi file backup (mount NFS) |
| `DATABASE_URL` | wajib | Koneksi MariaDB metadata; compose menyusunnya dari `DB_PASSWORD` |
| `SECRET_KEY` | wajib | Minimal 32 karakter, untuk tanda tangan token CSRF |
| `NAS_STATUS_STALE_SECONDS` | 300 | Batas umur hasil pemeriksaan NAS; lewat dari ini pengecek dianggap berhenti |
| `CHECK_INTERVAL_SECONDS` | 60 | Jeda pengecek NAS dan alert |
| `MOUNT_MARKER` | `.lumbung-mounted` | File penanda NFS; kosongkan untuk menonaktifkan cek |
| `MOUNT_PROBE_TIMEOUT_SECONDS` | 10 | Batas tunggu pemeriksaan NAS |
| `UPLOAD_MAX_CONCURRENT` | 4 | Batas upload yang diproses bersamaan |
| `LOGIN_MAX_FAILURES` | 5 | Batas login gagal per username dan alamat IP |
| `LOGIN_MAX_FAILURES_PER_IP` | 50 | Batas login gagal per alamat IP untuk semua username |
| `LOGIN_FAILURE_WINDOW` | 900 | Jendela hitungan login gagal, dalam detik |
| `SESSION_TTL`, `SESSION_MAX_AGE` | 43200 | Umur sesi login dalam detik. Keduanya sama, jadi aktivitas tidak memperpanjang sesi |
| `TRUSTED_PROXY_IPS` | `172.30.0.0/24` | IP reverse proxy, diteruskan ke uvicorn. Hanya dari IP ini `X-Forwarded-For` dan `X-Forwarded-Proto` dipercaya, untuk IP pengirim dan skema pemeriksaan `Origin` |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | kosong | Tujuan notifikasi; kosong berarti hanya masuk log |
| `RUNNERS_FILE` | `/config/runners.conf` | Tahap 2: lokasi file daftar runner, terpisah dari agen |
| `RUNNER_DOWNLOAD_MAX_CONCURRENT` | 2 | Tahap 2: batas unduhan runner bersamaan, terpisah dari batas upload |
| `RUNNER_DOWNLOAD_MAX_MBPS` | 500 | Tahap 2: batas kecepatan per unduhan |
| `RUNNER_MAX_TOTAL` | 2 | Tahap 2: batas tugas bersamaan di semua runner; tiap runner mengerjakan satu tugas |
| `RESTORE_TEST_DEFAULT` | Tiap Minggu 03:30 | Tahap 2: jadwal uji untuk container baru; sesudah itu diubah per container |
| `INSTALL_COMMAND_HOURS` | 2 | Batas waktu perintah pasang di Tambah container |
| `MANUAL_RESTORE_MAX_HOURS` | 24 | Tahap 2: batas waktu terpanjang satu sesi Restore manual, termasuk perpanjangan |

Nilai `RUNNER_*` di atas masih usulan dan perlu diukur dengan NAS asli.

Variabel lain yang ditampilkan di Pengaturan prototype, semuanya masih usulan:

| Variabel | Default | Fungsi | Tahap |
| --- | --- | --- | --- |
| `RETENTION_DAILY_DAYS` | 30 | Simpan backup harian selama N hari | 2 |
| `RETENTION_MONTHLY` | 12 | Simpan full tanggal 1 selama N bulan | 2 |
| `RETENTION_BINLOG_DAYS` | 3 | Simpan binlog selama N hari | 2 |
| `RETENTION_DRY_RUN` | `true` | Mode uji coba: hanya dicatat, tidak ada yang dihapus | 2 |
| `NOTIFY_RETRY` | 5 menit, bertahap hingga 1 jam | Jeda kirim ulang notifikasi | 2 |
| `METRICS_LOAD_INTERVAL` | Sampel tiap 60 detik, disimpan per jam | Pengambilan metrik beban | 3 |
| `METRICS_SIZE_INTERVAL` | 1 hari | Pengambilan ukuran tabel | 3 |
| `REPORT_DAILY_AT` | 07:00 | Waktu terbit laporan harian | 3 |
| `REPORT_WEEKLY_AT` | Senin 07:00 | Waktu terbit laporan mingguan | 3 |
| `AI_PROVIDER` | `claude` | Penyedia model | 4 |

#### Daftar agen (`agents.conf`)

Semua agen dan tokennya hanya tersimpan di satu tempat: file `config/agents.conf`, dipasang ke container di `/config/agents.conf`. Tidak ada di `.env` dan tidak ada di database. File ini juga satu-satunya sumber status aktif; tabel `containers` hanya menyimpan waktu terdaftar dan asal container. Satu baris satu agen:

```conf
# LumbungDB: daftar agen
# Format: nama = token
#   - nama: huruf, angka, _ . - (diawali huruf atau angka); menjadi nama folder di NAS
#   - token: minimal 16 karakter
# Satu baris untuk satu container MariaDB. Nama harus unik di seluruh LumbungDB.
# Awali baris dengan # untuk menonaktifkan agen. File di NAS tidak ikut terhapus.

simrs-utama = Hk3vP9qLw2Xz7Rt5Nb8M
siak-db     = Yd6Fs1Jc4Gm0Ue9Ka2Wp
# keuangan-lama = Qe7Lr3Tn8Vb1Mx5Zc0D
```

- Baris kosong dan baris yang diawali `#` diabaikan. Agen yang dikomentari ditolak (401), tetapi riwayat dan filenya tetap tampil di dashboard dengan status nonaktif.
- Ganti token, mengaktifkan lagi, dan menghapus agen dilakukan dengan mengedit file ini, bukan lewat dashboard. Setelah mengubahnya, jalankan `docker compose restart lumbung`.
- Tambah container di dashboard membuat token acak, menambahkan baris `nama = token` di akhir file, lalu memuat ulang daftar agen tanpa restart. Karena itu file dipasang read-write dan hanya bisa ditulis oleh proses LumbungDB. Selain itu, dashboard hanya bisa menonaktifkan agen (menu ⋮ di detail container): ia memberi `#` di awal baris agen tersebut lalu memuat ulang daftar agen. Dashboard tidak pernah mengubah token, menghapus baris, atau menghapus tanda `#`. Satu pengecualian: Batalkan pendaftaran di Tambah container menghapus baris agen yang baru dibuat di wizard itu, selama agen belum pernah mengirim heartbeat.
- LumbungDB gagal start dengan pesan yang menyebut nomor baris bila ada baris tidak valid, nama ganda, dua nama yang menghasilkan nama project compose yang sama, atau token kurang dari 16 karakter.

#### Akun admin

Tiap orang punya akun sendiri, supaya kolom "siapa" di `control_log` dan `restore_log` berisi nama orangnya. Tidak ada pembagian peran: semua akun bisa memakai semua aksi, termasuk mengelola akun lain. Akun dikelola di Pengaturan › Akun admin.

- Akun pertama dibuat dari terminal: `docker compose run --rm web python -m app.auth.cli create-user <username>`. Password dibaca dari prompt, tidak pernah dari argumen. Selama belum ada akun, dashboard tidak bisa dibuka.
- Admin bisa menambah akun, menonaktifkan, mengaktifkan lagi, dan mereset password akun lain. Password awal dan hasil reset hanya tampil sekali dan wajib diganti saat login berikutnya.
- Akun tidak bisa dihapus, hanya dinonaktifkan, supaya nama di riwayat tetap jelas. Admin tidak bisa menonaktifkan akunnya sendiri, jadi selalu ada satu akun aktif.
- Bila semua password hilang, admin server menjalankan `docker compose exec web python -m app.auth.cli set-password <username>`.
- Password minimal 12 karakter dan disimpan sebagai hash Argon2id.
- Login gagal ditahan menurut `LOGIN_MAX_FAILURES` per username dan alamat IP, dan `LOGIN_MAX_FAILURES_PER_IP` per alamat IP, dalam jendela `LOGIN_FAILURE_WINDOW`. Penyerang dari IP lain tidak bisa mengunci akun korban.
- Sesi disimpan di cookie `HttpOnly`, `SameSite=Strict`, dan `Secure` (`COOKIE_SECURE`). Yang tersimpan di database hanya hash token sesinya; token CSRF diturunkan dari hash itu dengan HMAC. Sesi berakhir setelah `SESSION_MAX_AGE`, jadi penyegaran otomatis tidak membuatnya hidup terus, dan monitor kantor perlu login lagi.
- Menonaktifkan akun, mengganti password, dan mereset password langsung mengakhiri semua sesi akun itu.
- Tiap login, login gagal, dan perubahan akun dicatat di `user_log`.

### Agen (`mariadb/agent/agent.py`)

Satu proses Python di dalam container MariaDB, yang menjadi bagian dari image `zmutclik/mariadb:lumbungdb-*` dan berjalan sebagai user `lumbung`, bukan root. Bila satu server menjalankan beberapa container, tiap container punya agen dan token sendiri. Rinciannya di Rancangan image MariaDB dan kendali backup.

- Tiap `INTERVAL_SECONDS` (60) agen mengirim heartbeat, lalu memindai `fullbackup/*.gz`, `databases/*/*.gz`, dan `binlog/*.gz`.
- Heartbeat berjalan di thread sendiri, supaya upload file besar tidak terbaca sebagai agen mati oleh alert heartbeat 10 menit.
- File dikirim bila ukuran dan waktu ubahnya tidak berubah selama `STABLE_SECONDS` (120), supaya file yang sedang ditulis tidak terkirim.
- File yang sudah terkirim dicatat di `/backup/.lumbung/agent/sent.json` beserta ukuran dan waktu ubahnya, dan tidak dikirim ulang. File bernama sama dengan ukuran atau waktu ubah berbeda dianggap file baru dan dikirim. Upload yang gagal otomatis dicoba lagi di putaran berikutnya.
- SHA256 dihitung sebelum upload, jadi tiap file dibaca dua kali. Isi file tidak dimuat ke memori.
- Heartbeat berisi identitas server, ruang disk `/backup`, versi MariaDB, jumlah file per jenis, dan 20 baris terakhir log backup. Nama mesin di dalam container berupa nama container, jadi agen membaca `/etc/hostname` dan `/etc/machine-id` milik mesin, yang di-mount read-only lewat compose ke `/lumbung/server/`, karena Docker mengelola `/etc/hostname` di dalam container. Nama server tidak pernah diisi admin.
- Tahap 2: heartbeat juga berisi jumlah dan ukuran file tertunda, waktu upload terakhir yang berhasil, serta error upload terakhir (kode, file, waktu percobaan pertama, jumlah percobaan). Satu file yang gagal tidak menghentikan pengiriman file lain.

| Variabel | Default | Fungsi |
| --- | --- | --- |
| `LUMBUNG_URL`, `LUMBUNG_TOKEN` | wajib | Alamat LumbungDB dan token milik container ini |
| `BACKUP_DIR` | `/backup` | Lokasi backup yang dibaca |
| `INTERVAL_SECONDS` | 60 | Jeda antar putaran |
| `STABLE_SECONDS` | 120 | Lama file tidak berubah sebelum dianggap selesai |
| `MAX_FILE_AGE_DAYS` | 0 | Lewati file lebih tua dari N hari; 0 berarti kirim semua |
| `BINLOG_UNSENT_MAX_DAYS` | 3 | Batas tahan arsip binlog yang belum terkirim, dibaca script backup; sesudahnya arsip dihapus |

### Runner (tahap 2)

Program terpisah dengan image dan compose sendiri, dipasang di VM atau server khusus. Boleh satu VM dengan LumbungDB, asalkan prosesnya terpisah dan disk scratch-nya sendiri. Uji dan cadangan sebuah container tidak berjalan di server yang sama dengan container itu.

Runner tidak punya dashboard atau jadwal sendiri. Ia mengambil tugas dari LumbungDB, mengunduh file lewat HTTPS, menjalankan tugas di mesinnya, lalu mengirim langkah, durasi, dan hasil. Data dari backup tetap di mesin runner.

| Variabel | Default | Fungsi |
| --- | --- | --- |
| `LUMBUNG_URL`, `LUMBUNG_RUNNER_TOKEN` | wajib | Alamat LumbungDB dan token milik runner ini |
| `SCRATCH_DIR` | wajib | Disk scratch terenkripsi untuk hasil ekstrak dan database cadangan |

Daftar runner dan tokennya ada di `config/runners.conf` dengan format dan aturan yang sama seperti `agents.conf`, jadi token agen tidak bisa mengambil tugas restore. Tambah runner di dashboard menampilkan token sekali, dan Nonaktifkan memberi `#` di baris runner.

Bila LumbungDB tidak terjangkau, tugas yang sedang berjalan tetap diselesaikan dan hasilnya dikirim saat koneksi pulih. Database cadangan tetap hidup, tetapi tidak ada tugas baru.

## Spesifikasi API

Endpoint baca dan kirim: agen memakai dua yang pertama dengan header `X-Token`, dashboard memakai dua berikutnya, dan healthcheck memakai yang terakhir. Aksi tulis dashboard dan endpoint runner dijelaskan sesudahnya.

| Endpoint | Auth | Fungsi |
| --- | --- | --- |
| `PUT /api/upload/{kind}/{relpath}` | `X-Token` | Kirim satu file backup sebagai body mentah. `kind` = `fullbackup`, `databases`, atau `binlog` |
| `POST /api/heartbeat` | `X-Token` | Kirim ringkasan kondisi container dan server sebagai JSON |
| `GET /api/status` | Sesi login | Status semua container dan daftar alert aktif, JSON |
| `GET /` | Sesi login | Dashboard HTML; tanpa sesi dialihkan ke halaman login |
| `GET /healthz`, `GET /readyz` | tanpa, hanya dari dalam | Healthcheck container, tanpa menyentuh NAS. Ditolak (403) bila lewat proxy |
| `GET /api/watchdog` | tanpa | Untuk pemeriksa di luar VM: status NAS terakhir dari database, 503 bila pengecek berhenti |

Aksi tulis dashboard memakai sesi login yang sama, dan wajib membawa token CSRF serta header `Origin` yang cocok dengan skema dari uvicorn dan `Host` yang diteruskan proxy apa adanya, karena browser mengirim cookie sesi secara otomatis. Jalur dashboard berupa form htmx yang menjawab fragmen HTML, bukan JSON. `GET /login`, `POST /login`, dan `POST /logout` melayani login; `POST /login` dijawab 429 saat penahanan berlaku.

| Endpoint | Fungsi | Tahap |
| --- | --- | --- |
| `POST /account/users` | Tambah akun admin, menjawab password awal sekali | 1 |
| `POST /account/users/{username}/disable`, `POST /account/users/{username}/enable` | Nonaktifkan atau aktifkan lagi akun; akun sendiri tidak bisa dinonaktifkan | 1 |
| `POST /account/users/{username}/reset-password` | Reset password, menjawab password baru sekali, dan mengakhiri sesi akun itu | 1 |
| `POST /account/password` | Ganti password sendiri | 1 |
| `POST /containers` | Tambah container: menulis baris ke `agents.conf`, menjawab perintah pasang dan token sekali | 1 |
| `POST /containers/{nama}/install-command` | Perintah pasang baru, selama agen belum terhubung; perintah lama langsung mati | 1 |
| `DELETE /containers/{nama}/pending` | Batalkan pendaftaran; ditolak bila agen sudah pernah mengirim heartbeat | 1 |
| `POST /containers/{nama}/disable` | Nonaktifkan: memberi `#` di baris agen | 1 |
| `PUT /containers/{nama}/schedule` | Ubah jadwal full, dump, dan binlog | 1 |
| `POST /containers/{nama}/requests` | Minta backup sekarang: full, dump semua atau satu database, atau binlog | 1 |
| `DELETE /containers/{nama}/requests/{id}` | Batalkan permintaan yang belum diambil agen; ditolak bila sudah diambil | 1 |
| `POST /api/containers/{nama}/restore-tests` | Jalankan uji restore sekarang, dengan cakupan dari jadwal | 2 |
| `PUT /api/containers/{nama}/schedules/test` | Ubah jadwal dan cakupan uji restore | 2 |
| `PUT /api/containers/{nama}/schedules/cadangan` | Ubah jadwal database cadangan; mematikannya menghapus database cadangan | 2 |
| `PUT /api/containers/{nama}/runners/test` | Ganti runner uji; berlaku mulai uji berikutnya | 2 |
| `PUT /api/containers/{nama}/runners/cadangan` | Ganti runner cadangan; database cadangan lama dihapus setelah yang baru lolos | 2 |
| `POST /api/containers/{nama}/cadangan/restore` | Restore sekarang ke mesin runner, dengan full yang dipilih | 2 |
| `POST /api/containers/{nama}/cadangan/promote` | Naikkan container cadangan ke production, dengan nama container baru | 2 |
| `POST /api/manual-restores` | Buat sesi Restore manual, menjawab token sekali; ditolak bila `agents.conf` tidak bisa ditulis atau NAS bermasalah | 2 |
| `POST /api/manual-restores/{id}/extend` | Perpanjang batas waktu 2 jam, paling lama `MANUAL_RESTORE_MAX_HOURS` sejak dibuat | 2 |
| `DELETE /api/manual-restores/{id}` | Cabut sesi yang masih aktif | 2 |
| `POST /api/runners` | Tambah runner: menulis baris ke `runners.conf`, menjawab token sekali | 2 |
| `DELETE /api/runners/{nama}/pending` | Batalkan pendaftaran runner yang belum pernah mengirim heartbeat | 2 |
| `POST /api/runners/{nama}/disable` | Nonaktifkan runner: memberi `#` di barisnya | 2 |
| `POST /integrity/{id}/review` | Tandai perbedaan checksum sudah ditinjau | 1 |
| `POST /api/rescan` | Pindai ulang NAS | 2 |
| `POST /api/notifications/retry`, `POST /api/notifications/test` | Kirim ulang antrean, kirim pesan uji | 2 |

Bila `agents.conf` atau `runners.conf` gagal ditulis, aksi dijawab 500, token tidak dibuat, dan daftarnya tidak berubah.

Jawaban `POST /api/heartbeat` berisi jadwal bila versinya berubah dan permintaan backup yang menunggu. Agen melaporkan kemajuan permintaan lewat `POST /api/agent/requests/{id}/status` dengan `X-Token`; token hanya bisa membaca dan mengubah permintaan milik container-nya.

Endpoint runner memakai header `X-Runner-Token` dan ada di tahap 2:

| Endpoint | Fungsi |
| --- | --- |
| `POST /api/runner/heartbeat` | Kirim versi, sisa disk scratch, dan status unduhan |
| `POST /api/runner/jobs/claim` | Ambil tugas berikutnya milik runner ini, berisi daftar file dan checksum |
| `GET /api/runner/jobs/{id}/files/{file}` | Unduh file sebagai stream. Mendukung header `Range` supaya unduhan yang putus bisa dilanjutkan |
| `POST /api/runner/jobs/{id}/progress` | Laporkan langkah yang sedang berjalan dan perpanjang batas waktu tugas |
| `POST /api/runner/jobs/{id}/result` | Kirim hasil: lolos, gagal, atau tidak terlaksana |

Unduhan dijawab 403 bila file bukan bagian dari tugas runner itu, dan 503 bila NAS bermasalah atau batas unduhan bersamaan tercapai. Tiap unduhan dicatat.

Script pasang memakai header `X-Install-Token` dan tidak butuh login:

| Endpoint | Fungsi |
| --- | --- |
| `GET /install.sh` | Script tanpa rahasia |
| `GET /install/docker-compose.lumbung.yml` | Compose referensi, salinan dari `mariadb/` yang dijaga sama oleh test |
| `POST /api/install/session` | Periksa token, ikat ke `machine-id`, jawab isi `.env` tanpa password, sebagai teks biasa |

Script Restore manual memakai header `X-Restore-Token`, ada di tahap 2, dan berbagi batas unduhan dengan runner:

| Endpoint | Fungsi |
| --- | --- |
| `GET /restore.sh` | Script tanpa rahasia, tanpa login |
| `POST /api/manual/session` | Periksa token, ikat ke `machine-id`, jawab daftar file, checksum, tag image, nama, dan port |
| `GET /api/manual/files/{file}` | Unduh file milik sesi sebagai stream, mendukung header `Range` |
| `POST /api/manual/progress` | Laporkan langkah yang sedang berjalan atau kegagalan |
| `POST /api/manual/register` | Tulis baris agen ke `agents.conf`, jawab token agen sekali, lalu matikan token restore |

Token yang salah, kedaluwarsa, atau dicabut dijawab 401, dan mesin selain yang terikat dijawab 403. Percobaan token salah dibatasi per alamat IP.

Upload hanya menerima nama berakhiran `.gz.age` dengan awal file format `age`; selain itu ditolak. Header opsional untuk upload: `X-SHA256` berisi checksum file terenkripsi. Bila ada dan tidak cocok, file ditolak. `X-Plain-SHA256` berisi checksum isi sebelum enkripsi, dan selalu dikirim agen; dipakai untuk mengenali kiriman ulang dan versi. `X-File-Mtime` berisi waktu ubah file di server, dan selalu dikirim agen; dipakai bila waktu di nama file tidak bisa dibaca. `X-Backup-Request` berisi ID permintaan, dikirim bila file adalah hasil permintaan backup. `X-Binlog-Start` berisi nama binlog awal, dikirim untuk full.

Aturan `relpath`: maksimal satu segmen untuk `fullbackup` dan `binlog`, dua segmen (`<db>/<file>`) untuk `databases`. Tiap segmen hanya boleh huruf, angka, `_`, `.`, `-`, dan tidak boleh diawali titik.

| Kode | Arti |
| --- | --- |
| 200 | Tersimpan (`stored`), sudah ada dengan isi sama (`exists`), atau tersimpan sebagai versi baru karena isinya berbeda (`version`) |
| 400 | `kind` atau nama file tidak valid, termasuk upaya path traversal |
| 401 | Token salah, atau login dashboard salah |
| 422 | Checksum tidak cocok atau file kosong; file tidak disimpan |
| 503 | NAS tidak ter-mount atau tidak merespons, atau batas upload bersamaan tercapai |

## Model data

Metadata ada di tabel MariaDB, dengan skema dari migrasi Alembic; isi file backup hanya ada di NAS, tidak pernah di database.

| Tabel | Kolom utama | Fungsi |
| --- | --- | --- |
| `containers` | `name` (kunci), `registered_at`, `origin`, `source_container` | Waktu terdaftar dan asal container: `dashboard` atau `file` di tahap 1, `promote` atau `manual` di tahap 2. Tanpa token |
| `backups` | `container`, `kind`, `database`, `relpath`, `stored_path`, `size`, `sha256`, `backup_at`, `received_at`, `request_id`, `chain_id`, `binlog_start` | Satu baris per file atau versi yang diterima, unik per `container`, `kind`, `relpath`, `sha256`. Dasar status "backup terakhir" |
| `heartbeats` | `container` (kunci), `server`, `machine_id`, `at`, `info` (JSON) | Heartbeat terakhir tiap container. Daftar server disusun dari sini |
| `nas_status` | `state`, `since`, `total_bytes`, `free_bytes`, `checked_at` | Hasil pemeriksaan NAS terakhir, satu baris |
| `alerts` | `key` (kunci), `container`, `cond`, `since`, `last_sent` | Alert yang sedang aktif, untuk mencegah notifikasi berulang |

Tabel yang ditambahkan tahap berikutnya:

| Tabel | Tahap | Fungsi |
| --- | --- | --- |
| `users` | 1 | Akun admin: username, hash password, status aktif, wajib ganti password, login terakhir |
| `user_sessions` | 1 | Sesi login: hash token, akun, batas waktu |
| `login_failures` | 1 | Percobaan login gagal per akun dan alamat IP, tanpa teks username |
| `user_log` | 1 | Riwayat login, login gagal, dan perubahan akun: siapa, kapan, dan alamat IP |
| `container_installs` | 1 | Perintah pasang terakhir tiap container: hash token, port, batas waktu, `machine-id` terikat |
| `integrity_events` | 1 | Perbedaan checksum untuk nama file yang sama, lokasi tiap versi, dan status tinjauan |
| `notifications` | 2 | Antrean kirim dan riwayat notifikasi |
| `runner_heartbeats` | 2 | Heartbeat terakhir tiap runner |
| `container_settings` | 2 | Runner uji, runner cadangan, jadwal dan cakupan uji, jadwal restore |
| `jobs` | 2 | Tugas runner, batas waktunya, dan statusnya |
| `job_downloads` | 2 | Catatan tiap unduhan runner |
| `restore_tests` | 2 | Hasil uji restore |
| `cadangan_runs` | 2 | Riwayat restore |
| `cadangan` | 2 | Kondisi database cadangan tiap container: full, data sampai, lokasi |
| `restore_log` | 2 | Restore sekarang, Naikkan ke production, dan Restore manual: siapa, kapan, dan apa |
| `manual_restores` | 2 | Sesi Restore manual: hash token, sumber, full, pilihan binlog, nama, port, batas waktu, `machine-id` terikat, langkah, dan status |
| `backup_schedules` | 1 | Jadwal tiap container: yang diminta LumbungDB beserta versinya, versi yang sudah diterapkan, dan jadwal yang berlaku menurut agen |
| `backup_requests` | 1 | Permintaan backup: jenis, peminta, status, waktu tiap status, hasil, dan file yang dihasilkan |
| `control_log` | 1 | Riwayat perubahan jadwal dan permintaan backup: siapa, kapan, nilai lama dan baru |
| `metrics` | 3 | Deret waktu metrik |
| `metric_counters` | 3 | Nilai kumulatif terakhir tiap penghitung `SHOW GLOBAL STATUS`, untuk selisih dan deteksi restart |
| `retention_log` | 2 | File yang dihapus retensi: siapa, kapan dibuat, dan alasannya |
| `cadangan`, `cadangan_runs` | 2 | Kondisi database cadangan (full, binlog terakhir, data sampai, port) dan riwayat restore |
| `reports` | 3 | Snapshot laporan saat terbit; teks AI dan input AI-nya menyusul di tahap 4 |

Struktur folder di NAS meniru struktur `/backup` di container. Folder tidak memakai nama server, supaya riwayat dan rantai backup tidak terpecah bila container pindah server:

```text
<BACKUP_ROOT>/
  .lumbung-mounted                     # penanda mount
  <nama-container>/
    fullbackup/<waktu>.xbstream.gz
    databases/<db>/<db>-<waktu>.sql.gz
    databases/_users/_users-<waktu>.sql.gz
    binlog/mysql-bin.<nomor>.gz
    binlog/mysql-bin.<nomor>.gz.v2-<sha8>   # versi baru: nama sama, isi berbeda, biasanya rantai baru
```

Baris `containers` dibuat saat Tambah container. Agen yang ditambahkan langsung di `agents.conf`, atau yang barisnya hilang karena database metadata hilang, dicatat saat daftar agen dimuat, dengan waktu muat sebagai `registered_at`. Kolom `backups.request_id` berisi ID permintaan backup; kosong berarti backup terjadwal.

Ada dua waktu per file:

- `backup_at` adalah waktu pembuatan backup, dan menjadi dasar umur backup, alert, linimasa, dan retensi. Nilainya diambil dari waktu di nama file; bila tidak bisa dibaca, dari `X-File-Mtime`. Waktu yang lebih dari 1 jam di depan jam LumbungDB tidak dipakai.
- `received_at` adalah waktu file diterima menurut jam VM LumbungDB. Kolom ini hanya untuk catatan dan dipakai sebagai `backup_at` bila dua sumber di atas tidak tersedia.

Binlog dan full dikaitkan lewat dua kolom:

- `chain_id` adalah nomor rantai binlog per container. LumbungDB menghitungnya saat file masuk: baris diurutkan menurut `backup_at` lalu nomor, dan nomor yang lebih kecil dari baris sebelumnya memulai rantai baru. Binlog lama yang terlambat terkirim tetap masuk rantai lamanya, karena waktu ubahnya tetap waktu lama.
- `binlog_start` adalah binlog awal sebuah full, dibaca agen dari file `<waktu>.xbstream.gz.info` yang ditulis `databasebackup.sh`. Bila kosong, misalnya pada full lama atau setelah pemindaian ulang, nilainya diisi runner dari `mariadb_backup_binlog_info` pada uji atau restore berikutnya.
- `chain_id` sebuah full adalah rantai yang memiliki nomor `binlog_start` dengan `backup_at` sesudah full dibuat.

Binlog untuk sebuah full adalah baris dengan `chain_id` yang sama dan nomor mulai dari `binlog_start`, diurutkan menurut nomor. Aturan ini dipakai alert rantai, retensi, uji restore, database cadangan, Restore sekarang, dan Restore manual. Posisi persis untuk memulai penerapan tetap dibaca dari `mariadb_backup_binlog_info` di hasil ekstrak.

"Backup terakhir" adalah baris dengan `backup_at` terbaru, jadi file lama yang baru terkirim, misalnya saat sinkronisasi pertama, tidak membuat status terlihat baru dan tidak memulihkan alert.

## Keamanan dan privasi

File backup database rumah sakit kemungkinan besar memuat data pasien, jadi NAS dan jalur kirimnya diperlakukan sebagai data sensitif. Rancangan tahap 1 menutup pemalsuan dan kerusakan file, tetapi enkripsi jalur kirim dan isi file masih harus ditambah.

Dirancang di tahap 1:

- Satu token per container, dibandingkan dengan `compare_digest`. Token menentukan nama container, sehingga agen tidak bisa menulis ke folder container lain.
- Token tidak pernah ditampilkan di dashboard, termasuk awalannya. Satu-satunya saat token tampil adalah sekali di Tambah container, ketika token baru dibuat, di bagian pasang manual. Script pasang mengambil token agen lewat token pasang, jadi token agen tidak masuk riwayat shell. Halaman detail dan daftar server tidak menampilkan token sama sekali, dan dashboard tidak punya tombol Ganti token. Di tahap 1, satu-satunya aksi di detail container adalah Nonaktifkan, disembunyikan di menu ⋮.
- Nama file dan `kind` divalidasi ketat; upaya `../` ditolak (400) dan hasil path dipastikan tetap di dalam folder container.
- Checksum SHA256 dicek di LumbungDB; file rusak atau terpotong ditolak dan tidak disimpan.
- Agen berjalan sebagai user non-root yang hanya bisa membaca `/backup` dan menulis ke `/backup/.lumbung/agent/`, jadi ia tidak bisa mengubah atau menghapus backup sumber.
- Dashboard dan `/api/status` memakai sesi login akun admin per orang; notifikasi Telegram hanya berisi nama container, nama database, dan umur backup, tanpa isi data atau log.

Perlu ditambah sebelum dipakai untuk data produksi:

1. **HTTPS.** LumbungDB melayani HTTP biasa, sehingga token dan file lewat jaringan tanpa enkripsi. Pasang reverse proxy dengan SSL beli, sertakan sertifikat perantara (full chain), dan buka port LumbungDB hanya untuk proxy. Layanan tetap hanya di jaringan lokal: DNS internal mengarahkan subdomain sungguhan ke IP proxy. Saat SSL diperpanjang, sertifikat hanya diganti di proxy; agen dan runner tidak menyimpannya.
2. **Izin NFS di Synology** hanya untuk IP VM LumbungDB, dan akses folder dibatasi ke akun yang perlu. Runner dan agen tidak diberi akses NFS. Satu pengecualian: saat pemulihan darurat, admin membuka izin read-only sementara untuk server tujuan, lalu mencabutnya.
3. **Enkripsi di agen** sebelum file dikirim, lihat Enkripsi backup. Kunci privat disimpan di luar LumbungDB, dan satu pemulihan dari file terenkripsi sudah dilatih dengan kunci pemulihan.
4. **Jalankan container LumbungDB sebagai user non-root.** Container ini tidak boleh memasang Docker socket. User-nya disesuaikan dengan pemetaan UID NFS.
5. **Izin file `.env`, `config/agents.conf`, dan `config/runners.conf` 600** karena berisi token Telegram, token agen, dan token runner.
6. **Perlindungan aksi tulis:** token CSRF dan pemeriksaan `Origin`, seperti di Spesifikasi API.
7. **Rotasi token** bila ada server yang dicurigai bocor: ganti tokennya di `config/agents.conf`, restart LumbungDB, lalu perbarui `LUMBUNG_TOKEN` di `.env` container tersebut dan buat ulang container-nya. MariaDB ikut restart. Langkah lengkapnya ada di bagian Merotasi token agen di `README.md`.

Perlu ada sebelum runner dipakai (tahap 2):

- **HTTPS wajib,** karena token dan file berisi data pasien lewat jaringan.
- **Akses file per tugas.** Runner hanya bisa mengunduh file dari tugasnya, dan tiap unduhan dicatat.
- **Mesin runner diperlakukan seperti produksi.** Scratch dan database cadangan berisi data pasien asli, jadi disknya terenkripsi dan akses adminnya terbatas. Uji dan cadangan sebuah container tidak berjalan di server yang sama dengan container itu; LumbungDB menahan tugas bila `machine-id` runner sama dengan milik container tersebut. Runner yang `machine-id`-nya tidak terbaca tidak diberi tugas.
- **Container uji terisolasi.** Container sementara berjalan dengan `--network none` dan event scheduler mati, supaya hasil restore tidak bisa menghubungi produksi.
- **Database cadangan tertutup.** Berjalan `read_only`, tanpa event scheduler, dan portnya hanya untuk admin, karena full ikut membawa akun dan password produksi.
- **Akun runner hanya di dalam container.** `lumbung_runner` hanya bisa masuk dari dalam container, password-nya tetap di disk scratch, dan akun ini dihapus sebelum hasil restore menjadi production.

Perlu ada sebelum Restore manual dipakai (tahap 2):

- **HTTPS dengan sertifikat tepercaya, tanpa `-k`.** Script dijalankan dengan akses Docker, jadi sertifikat proxy diperiksa dengan daftar CA bawaan server tujuan. Bila tidak lolos, perintahnya gagal, bukan melewati pemeriksaan.
- **Token sesi terbatas.** Token hanya bisa mengunduh file sesinya, terikat ke `machine-id` pertama, mati saat batas waktu, saat dicabut, dan saat pendaftaran berhasil. Yang disimpan hanya hash-nya, terpisah dari `agents.conf`.
- **Token tidak lewat URL.** Token dikirim lewat variabel lingkungan dan header, supaya tidak masuk log reverse proxy. Token tetap tercatat di riwayat shell, dan batas waktu serta ikatan mesin yang membatasi dampaknya.
- **Script utuh atau tidak sama sekali.** Isi script dibungkus fungsi yang baru dipanggil di baris terakhir, jadi unduhan yang terputus tidak menjalankan apa pun. Admin bisa mengunduh dan membacanya lebih dulu.
- **Disk tujuan diperlakukan seperti produksi.** Data pasien asli sampai di server itu. Script menghapus unduhan dan hasil ekstrak sesudah selesai atau gagal permanen, dan menulis `.env` dengan izin 600.
- **Bukan agen tetap.** Script hanya berjalan saat admin menempelkannya dan berhenti sesudah selesai, jadi tidak ada program pengelola Docker yang tinggal di server.

Perlu ada sebelum image dipakai:

- **Daftar aksi tertutup.** LumbungDB hanya bisa mengirim jadwal dan jenis backup, tidak pernah teks perintah. Bila LumbungDB dibobol, penyerang bisa mengubah atau mematikan jadwal dan memicu backup berulang, tetapi tidak menjalankan perintah bebas di container.
- **Validasi di agen, bukan hanya di LumbungDB.** Jadwal harus berupa lima kolom cron dengan karakter `0-9 * / , -`, atau `off`. Nama database hanya huruf, angka, `_`, `-`. Script dijalankan langsung dengan daftar argumen, tanpa shell. Helper memvalidasi ulang dengan aturan yang sama.
- **Agen bukan root.** Agen berjalan sebagai user `lumbung` dengan env bersih, tanpa `MARIADB_ROOT_PASSWORD`, `MARIADB_PASSWORD`, dan `BACKUP_PASS`. User ini tidak bisa membaca `/etc/db_backup.cnf`, datadir, atau env proses MariaDB, dan tidak bisa mengubah file backup. Agen tidak perlu login ke MariaDB di tahap 2.
- **Helper root yang sempit.** Yang butuh root hanya `lumbung-helper`, script kecil milik root yang dipanggil agen lewat `sudo` dengan satu aturan tanpa password. Helper hanya menerima dua aksi: `apply-schedule` (validasi jadwal, tulis crontab, ganti `schedule.json`) dan `run-backup` (`full`, `dump` dengan nama database opsional, atau `binlog`). Compose tidak memasang `no-new-privileges`, karena `sudo` membutuhkannya.
- **Agen tidak terpisah dari data.** Agen satu container dengan database, jadi celah pada agen bisa membaca file backup, memakai jatah CPU dan disk, serta lewat helper mengubah jadwal atau memicu backup. Celah itu tidak langsung memberi akses ke datadir dan password. Agen tidak membuka port dan hanya memakai library bawaan Python.
- **Perubahan tercatat.** Tiap perubahan jadwal dan permintaan backup masuk `control_log`.

### Enkripsi backup

Backup dienkripsi di agen dengan `age` sebelum meninggalkan container, jadi NAS dan LumbungDB hanya memegang file terenkripsi. Bila VM LumbungDB atau NAS dibobol, isi backup tetap tidak terbaca.

- **Kunci publik di server, kunci privat di luar.** LumbungDB menyimpan kunci publik di `BACKUP_AGE_RECIPIENTS` dan menyertakannya di `.env` container sebagai `LUMBUNG_AGE_RECIPIENTS`. Container hanya memegang kunci publik.
- **Dua penerima.** Tiap file dienkripsi untuk kunci runner dan kunci pemulihan, dan salah satunya cukup untuk membukanya. Kunci runner ada di mesin runner (tahap 2). Kunci pemulihan disimpan offline, misalnya di dua tempat fisik terpisah.
- **Kunci hilang berarti backup hilang.** Tidak ada cara memulihkan file tanpa salah satu kunci privat. Latih pemulihan dengan kunci pemulihan sebelum data produksi masuk.
- **Tanpa kunci publik tidak ada kiriman.** LumbungDB menolak start bila nilainya tidak valid, dan agen tidak mengirim file bila nilainya kosong, lalu melaporkannya sebagai error upload. Tidak ada jalur kirim tanpa enkripsi.
- **Penerima menolak file tanpa enkripsi.** Nama harus berakhiran `.gz.age` dan awal file harus format `age`. Pemeriksaan ini hanya membuktikan format, bukan siapa penerimanya.
- **Pengenal versi.** Hasil enkripsi selalu berbeda walaupun isinya sama, jadi kiriman ulang dikenali dari `X-Plain-SHA256`, checksum isi sebelum enkripsi. Checksum ini menunjukkan apakah dua file sama, tetapi tidak membuka isinya. Bila metadata hilang, file yang dikirim ulang menjadi versi baru di NAS karena tidak bisa dibandingkan.
- **Yang tetap terlihat.** Nama database, waktu, dan ukuran file tetap terbaca di nama file dan metadata.
- **Di server database.** File di `/backup` tetap tidak terenkripsi, karena script backup dan restore lokal memakainya. Saat mengirim, agen membuat salinan terenkripsi sementara di `/backup/.lumbung/agent/spool` lalu menghapusnya, jadi volume itu perlu ruang kosong sebesar file terbesar.
- **Ganti kunci.** Mengganti penerima hanya berlaku untuk file baru, jadi kunci privat lama tetap disimpan selama file lama masih ada di NAS.

## Deployment dan operasional

Pemasangan awal dikerjakan berurutan; langkah 1 sampai 3 sekali saja, langkah 4 diulang untuk tiap container MariaDB, dan langkah 6 untuk tiap runner. LumbungDB dipasang di VM khusus, di luar mesin fisik server database, supaya tidak ikut mati bersama database.

1. Di Synology: buat shared folder `lumbungdb`, aktifkan NFS, izinkan hanya IP VM LumbungDB.
2. Di VM LumbungDB: mount di server lewat `/etc/fstab` dengan `nfs4,hard,_netdev,nofail`, lalu buat penanda `touch <mount>/.lumbung-mounted`.
3. Salin `.env.example` menjadi `.env` dengan izin 600 dan isi `BACKUP_ROOT`, `DB_PASSWORD`, `SECRET_KEY`, `TRUSTED_PROXY_IPS`, serta Telegram. Letakkan sertifikat full chain di `deploy/certs/`. Salin `config/agents.conf.example` menjadi `config/agents.conf` dengan izin 600. Isinya boleh kosong; agen bisa ditambahkan langsung di file atau lewat Tambah container di dashboard. Lakukan hal yang sama untuk `config/runners.conf` di tahap 2. Lalu jalankan `docker compose up -d db`, `docker compose run --rm web alembic upgrade head`, buat akun pertama lewat `app.auth.cli`, dan `docker compose up -d --build`. Migrasi dijalankan sekali per deploy, sebelum `up`.
4. Untuk tiap container MariaDB: buka Tambah container di dashboard, lalu jalankan perintah `curl` yang tampil di server database. Script memasang `.env` dan compose di subfolder `<project>/` pada folder tempat perintah dijalankan, dan menjalankan `docker compose -p <project>`. Nama project yang berbeda mencegah beberapa container di satu server bentrok. Container lama mengikuti Perpindahan dari `zmutclik/mariadb`.
5. Verifikasi: tekan Backup sekarang di detail container, tunggu sampai statusnya Selesai, lalu pastikan file muncul di NAS.
6. Tahap 2, di tiap mesin runner: siapkan disk scratch terenkripsi, pastikan mesin bisa menjangkau LumbungDB lewat HTTPS dan registry image, isi `.env.runner`, lalu `docker compose -f docker-compose.runner.yml --env-file .env.runner up -d`. Tambah runner di dashboard menyediakan isi dan perintahnya.

Catatan operasional:

- Mount NFS memakai `rslave` di compose, jadi bila NFS ter-mount ulang di server, container ikut melihatnya. Selama NFS mati, container tetap berjalan, alert tetap terkirim, upload ditolak dan agen mengulang otomatis. Upload yang sedang menulis saat NAS macet bisa tersangkut sampai NAS kembali atau container di-restart. Dashboard menampilkan banner di semua halaman, dan uji restore serta retensi ditunda.
- Sinkronisasi pertama mengirim semua backup yang ada (retensi sumber 30 hari). Pakai `MAX_FILE_AGE_DAYS` bila tidak ingin mengirim semuanya.
- Database metadata ada di volume `lumbung_state`. Bila hilang, file backup di NAS tetap aman, tetapi riwayat dan status hilang sampai agen mengirim file baru. Akun admin juga hilang, dan akun pertama dibuat lagi lewat `app.auth.cli`. Pemindaian ulang NAS untuk membangun kembali metadata belum ada (rencana tahap 2).
- Restore dari file di NAS mengikuti prosedur di README image `zmutclik/mariadb:lumbungdb-*`: dump per database untuk satu database, full backup ke volume baru untuk seluruh container, binlog untuk point-in-time recovery. Dashboard belum menyusun perintahnya, jadi admin menjalankannya sendiri (lihat Restore manual). Runbook-nya harus tersimpan di luar aplikasi, supaya tetap bisa dipakai saat LumbungDB mati.
- Alert tidak bisa melaporkan matinya VM LumbungDB sendiri. Pasang pemeriksa `/api/watchdog` dari luar VM, misalnya dari mesin lain atau NAS.
- Backup hanya berisi data. Tag image, compose, `my.cnf`, dan env container perlu dicatat terpisah untuk pemulihan ke mesin baru, tanpa menyimpan password mentah.
- Setelah pulih di server baru dan hasilnya diperiksa, jalankan container dengan `LUMBUNG_*` dan token yang sama. Server baru terbaca otomatis dari heartbeat. Nomor binlog yang kembali kecil dianggap rantai baru.

## Alert

LumbungDB menilai enam kondisi tiap menit dan mengirim satu notifikasi saat kondisi muncul, satu pengingat tiap 12 jam bila belum pulih, dan satu pesan "Pulih" saat kembali normal.

| Kondisi | Variabel | Default |
| --- | --- | --- |
| NAS/NFS tidak ter-mount atau tidak merespons | `MOUNT_MARKER`, `MOUNT_PROBE_TIMEOUT_SECONDS` | pada pemeriksaan berikutnya, paling lama sekitar 70 detik |
| Ruang NAS tersisa kurang dari | `ALERT_DISK_FREE_PERCENT` | 10% |
| Agen tidak mengirim heartbeat selama | `ALERT_HEARTBEAT_MINUTES` | 10 menit |
| Full backup terakhir dibuat lebih lama dari | `ALERT_FULL_HOURS` | 36 jam |
| Dump terakhir satu database dibuat lebih lama dari | `ALERT_DUMP_HOURS` | 36 jam |
| Dump `_users` terakhir dibuat lebih lama dari | `ALERT_USERS_HOURS` | 36 jam |

Alert dump dinilai per database dan per container, jadi dump yang masih masuk tidak menutupi database lain yang gagal. Database yang dinilai adalah yang punya dump dengan `backup_at` dalam `ALERT_DB_EXPECTED_DAYS` (30) hari terakhir. Bila sebuah database dihapus, alertnya berjalan paling lama selama masa itu, lalu berhenti dengan pesan "tidak lagi dipantau", bukan "Pulih". Database yang belum pernah punya dump belum terdeteksi di tahap 1; tahap 3 menutupnya dengan membandingkan daftar database dari MariaDB.

`_users` berisi akun dan hak akses, bukan database. Karena itu `_users` punya alert sendiri, tidak dihitung dalam jumlah database, dan punya cara uji restore sendiri.

Kendali backup menambah kondisi berikut:

| Kondisi | Variabel | Default |
| --- | --- | --- |
| Agen menolak atau gagal menerapkan jadwal | tanpa ambang | saat dilaporkan agen |
| Jadwal full dimatikan | tanpa ambang | selama dimatikan |
| Permintaan backup gagal atau tidak selesai | tanpa ambang | saat statusnya berubah; pulih saat backup jenis itu berikutnya berhasil |

Ambang umur full dan dump dihitung dari jadwal yang dilaporkan agen: 1,5× jarak terpanjang antar jadwal, jadi jadwal harian tetap 36 jam dan jadwal mingguan menjadi 252 jam. `ALERT_FULL_HOURS`, `ALERT_DUMP_HOURS`, dan `ALERT_USERS_HOURS` dipakai sampai agen melaporkan jadwal pertamanya. Jenis yang dimatikan tidak dinilai umurnya; dump dan binlog yang dimatikan diberi label tetap di dashboard, sedangkan full memicu alert di atas.

Tahap 2 menambah kondisi berikut:

| Kondisi | Variabel | Default |
| --- | --- | --- |
| Rantai binlog putus selama | `ALERT_BINLOG_GAP_MINUTES` | 30 menit |
| Satu file gagal terkirim sejak percobaan pertama selama | `ALERT_UPLOAD_STALL_MINUTES` | 120 menit |
| Uji restore gagal | tanpa ambang | saat hasil uji diterima |
| Runner tidak mengirim heartbeat selama | `ALERT_RUNNER_HEARTBEAT_MINUTES` | 10 menit |
| Runner gagal mengunduh file backup | tanpa ambang | saat runner melaporkannya |
| Sisa disk scratch runner kurang dari | `ALERT_RUNNER_SCRATCH_FREE_PERCENT` | 15% |
| Runner dinonaktifkan padahal masih menangani container | tanpa ambang | saat dinonaktifkan |
| Database cadangan tidak di-restore selama | `ALERT_CADANGAN_LATE` | 2× jadwal restore |
| Runner satu server dengan container yang ditanganinya | tanpa ambang | sampai container memakai runner lain |
| Container hasil Naikkan ke production belum punya runner di server lain | tanpa ambang | sampai runner ditetapkan |

Rantai binlog dinilai per `chain_id` dari tabel `backups`, pada rantai yang terbaru, mulai dari `binlog_start` full terakhir yang lolos uji restore sampai binlog terbaru; bila rantai itu belum punya full yang lolos, mulai dari binlog tertua yang disimpan di rantai itu. Jeda 30 menit memberi waktu bagi upload yang datang terlambat. Alert pulih bila file yang hilang masuk, atau ada full lebih baru yang lolos uji. Nomor yang kembali kecil memulai rantai baru, bukan lompatan.

Alert upload dan alert unduhan runner tidak dinilai selama NAS tidak ter-mount, supaya satu penyebab tidak menjadi banyak alert. Alert upload memakai data heartbeat. Batas 120 menit memberi ruang untuk full berukuran besar; sesuaikan bila jaringan lebih lambat.

Uji yang tidak terlaksana karena masalah di sisi runner tidak memicu alert "Uji restore gagal"; yang muncul adalah alert runner.

Belum ada di prototype: bila semua agen di satu server berhenti bersamaan, kirim satu alert untuk server itu, dengan bunyi "agen di server X tidak merespons", karena dari sisi LumbungDB server mati dan jaringan putus terlihat sama. Alert disk juga dinilai per disk, karena beberapa container bisa berbagi disk yang sama.

Selain kondisi di atas, file yang tersimpan sebagai versi baru memicu alert "Checksum berbeda untuk file bernama sama", kecuali binlog yang masuk rantai berbeda. Alert ini tidak memakai ambang waktu dan pulih setelah perbedaannya ditandai sudah ditinjau.

Pengingat diatur `RENOTIFY_HOURS` (12). Container yang belum pernah mengirim apa pun juga dihitung bermasalah, jadi container baru langsung muncul sebagai alert sampai backup pertamanya masuk. Container yang sudah mengirim dump tetapi belum punya full berstatus Menunggu full pertama, dan menjadi alert bila full belum masuk 36 jam setelah `registered_at`.

Alert tidak bisa diabaikan atau dibisukan. Bila kelak dibutuhkan, rancangannya berupa pembisuan dengan alasan dan batas waktu, hanya untuk alert yang tidak keras.

Umur file binlog sengaja tidak dijadikan alert: saat database sepi tidak ada binlog baru, sehingga alert itu akan palsu. Jalur binlog dipantau lewat alert rantai putus dan alert upload di atas.

Batas tahap 1: bila Telegram gagal dijangkau, kegagalan hanya dicatat di log LumbungDB dan alert baru dicoba lagi saat pengingat 12 jam berikutnya.

## Roadmap

Pengembangan dibagi empat tahap berurutan. Aplikasi baru dipakai setelah keempatnya selesai (keputusan 3 November 2026), jadi tahap berikutnya boleh dibangun dengan data contoh sebelum gerbang tahap sebelumnya terbukti. Semua gerbang wajib lolos di lingkungan nyata sebelum pemakaian pertama. Belum ada tanggal yang ditetapkan.

| Urutan | Isi | Syarat lanjut |
| --- | --- | --- |
| Tahap 1 | Pengumpulan backup ke NAS, image `zmutclik/mariadb:lumbungdb-*`, kendali backup, dashboard status, dan alert Telegram | Lihat gerbang di bawahnya |
| Gerbang 1: Siap produksi | Tahap 1 terbukti dan aman untuk data produksi | Skenario di bagian Pengujian lolos dengan container dan NFS sungguhan; syarat Selesai bila di Rancangan image terpenuhi; versi file, `backup_at`, dan pemeriksaan NAS terpisah sudah berjalan; butir 1 sampai 6 di daftar "Perlu ditambah sebelum dipakai untuk data produksi" selesai, dan prosedur rotasi token tertulis |
| Tahap 2 | Retensi, runner, uji restore, database cadangan, Restore sekarang, Restore manual, pemindaian ulang NAS, alert binlog dan upload, antrean notifikasi | Lihat gerbang di bawahnya |
| Gerbang 2: Restore terbukti | Backup terbukti bisa dipulihkan | Syarat Selesai bila di Rancangan tahap 2 terpenuhi |
| Tahap 3 | Metrik kesehatan database dan laporan harian serta mingguan | Lihat gerbang di bawahnya |
| Gerbang 3: Laporan andal | Angka laporan terbukti dari data nyata | Syarat Selesai bila di Rancangan tahap 3 terpenuhi |
| Tahap 4 | Lapisan AI di atas laporan | Syarat Selesai bila di Rancangan tahap 4 terpenuhi |

Data produksi baru boleh masuk setelah semua gerbang terbukti, termasuk "Siap produksi" (lihat Keamanan dan Pengujian). Uji singkat NFS sungguhan dengan data contoh sebaiknya dilakukan sebelum tahap 2 dibangun terlalu jauh, karena tahap 2 bergantung pada asumsi NFS yang sama. Image `zmutclik/mariadb:lumbungdb-*` dan kendali backup termasuk syarat gerbang ini, karena sidecar tidak dipasang ke produksi.

Isi tahap 2, yang belum punya bagian rancangan sendiri:

- **Retensi di NAS.** Hapus backup yang melewati kebijakan, tanpa pernah menghapus satu-satunya backup yang masih valid. Usulan awal: harian 30 hari, bulanan 12 bulan, binlog 3 hari. Akibatnya point-in-time recovery hanya tersedia untuk 3 hari terakhir; backup yang lebih tua dipulihkan ke waktu backup itu dibuat.
- **Runner.** Program terpisah untuk uji restore dan database cadangan. Rinciannya di Rancangan tahap 2.
- **Uji restore terjadwal.** Jadwal dan cakupan per container, ke container sementara. Tidak menyentuh database produksi.
- **Database cadangan terjadwal.** Hanya container yang diaktifkan, dari full lalu binlog.
- **Restore sekarang.** Pemulihan seluruh container lewat LumbungDB, dijalankan runner cadangan tanpa menunggu jadwal.
- **Restore manual.** Pemulihan seluruh container ke server mana pun lewat satu perintah, tanpa runner. Rinciannya di Rancangan tahap 2.
- **Pemindaian ulang NAS** untuk membangun kembali metadata bila database metadata hilang.
- **Alert rantai binlog putus dan upload gagal,** termasuk data antrean kirim di heartbeat. Aturannya ada di bagian Alert.
- **Antrean kirim ulang** untuk notifikasi yang gagal.

Tampilan dashboard mengikuti tahap supaya fitur yang belum ada tidak terlihat:

| Tahap | Yang ditambahkan ke tampilan |
| --- | --- |
| 1 | Dashboard (ruang NAS, container bermasalah, alert, linimasa), menu Container yang dikelompokkan per server, detail container, jadwal backup, Backup sekarang, riwayat file, tinjauan checksum, daftar alert, Pengaturan, halaman login, akun admin |
| 2 | Rantai binlog dan antrean kirim, menu Runner, tab Uji restore, Cadangan, dan Restore manual di detail container, retensi, antrean notifikasi, pindai ulang NAS |
| 3 | Kesehatan, ukuran backup, laporan berisi angka |
| 4 | Ringkasan dan saran AI |

## Rancangan image MariaDB dan kendali backup

Agen ada di dalam image `zmutclik/mariadb:lumbungdb-*`, sehingga LumbungDB bisa mengatur jadwal semua container dan meminta backup tanpa membuka port atau memegang Docker socket. Yang membuat backup tetap cron dan script di dalam container, jadi backup tetap berjalan saat LumbungDB mati.

```text
Dashboard  --simpan-->  LumbungDB: jadwal versi N, permintaan backup
                             ^
                             |  heartbeat tiap 60 detik; jawabannya berisi perubahan
                             |
Container MariaDB:  agen --> crontab --> script --> /backup --> agen --> upload
                      `---> script (permintaan backup) --^
```

### Image sebagai referensi pemasangan

- Source image dari project `mariadb` pindah ke folder `mariadb/` di project LumbungDB, bersama riwayat git-nya, dan dipublikasikan di repo `zmutclik/mariadb` dengan tag `lumbungdb-<versi>`, misalnya `lumbungdb-12.3-a1`, menggantikan tag lama di repo itu. `build-image.sh` ikut memakai tag baru. Kode agen ada di `mariadb/agent/` dan ikut masuk image saat build.
- Compose referensi `mariadb/docker-compose.lumbung.yml` mengambil tag dari `IMAGE_TAG` di `.env`, tanpa nilai bawaan, jadi compose gagal bila variabel ini kosong. Tag berisi versi MariaDB dan versi agen, misalnya `12.3-a1`, bukan `latest`. Tag yang sama untuk versi MariaDB memastikan pembaruan agen tidak ikut menaikkan MariaDB.
- Tambah container di dashboard menyediakan satu perintah pasang, dan untuk container lama juga isi `.env` serta perintah `docker compose -p <project> -f docker-compose.lumbung.yml --env-file .env up -d`. Isi `.env` mencakup `LUMBUNG_URL`, yang mengikuti alamat dashboard yang sedang dibuka admin, `LUMBUNG_TOKEN`, `IMAGE_TAG`, `BINLOG_UNSENT_MAX_DAYS`, serta `CONTAINER_NAME` dan `CONTAINER_HOSTNAME` yang berisi nama container; identitas server dibaca agen dari mesinnya. Token tampil sekali seperti sekarang. User dan password database dibuat acak oleh script di server, atau diisi admin pada pasang manual, dan tidak pernah disimpan atau dibuat LumbungDB.
- Perintah pasangnya `curl -fsSL <url>/install.sh | LUMBUNG_URL=<url> INSTALL_TOKEN=lmi_xxx sh`. Aturannya: token berlaku `INSTALL_COMMAND_HOURS`, terikat ke mesin pertama yang memakainya, dan mati setelah agen mengirim heartbeat atau saat diganti perintah baru. Script memeriksa Docker dan port, menolak volume atau container yang sudah ada, membuat `.env` izin 600 dengan user dan password acak, menjalankan compose, lalu menunggu MariaDB sehat. Dijalankan ulang di mesin dan folder yang sama, script memakai `.env` yang sudah ada, jadi user dan password tidak berubah. Admin mengisi nama dan port; nama database diturunkan dari nama container, sedangkan user aplikasi (`app_<acak>`) dan user backup (`backup_<acak>`) dibuat acak. User `root` tetap bernama `root`, dengan password acak. Pasang manual untuk container lama tetap menyebut `user_backup`, karena user itu sudah ada di volume-nya. Script bersifat statis, jadi checksum-nya bisa disimpan di runbook.
- `<project>` diturunkan dari nama container: huruf kecil, dan karakter selain huruf, angka, `_`, `-` menjadi `-`. Nama yang menghasilkan project sama dengan container lain ditolak, misalnya `rad.db` dan `rad-db`, karena di satu server keduanya akan berbagi volume.
- Container sementara, database cadangan, dan container hasil restore memakai image yang sama, jadi dijalankan tanpa `LUMBUNG_*` dan dengan `BACKUP_CRON`, `DUMP_CRON`, serta `BINLOG_CRON` berisi `off`. Tanpa aturan ini, salinan bisa membuat backup sendiri, atau mengirim heartbeat atas nama container asli bila `.env`-nya ikut tersalin.
- Bila `LUMBUNG_URL` kosong, agen tidak berjalan dan image bekerja seperti sekarang: cron tetap membuat backup di `/backup`, dan arsip binlog dihapus setelah full berhasil tanpa memeriksa catatan terkirim.
- Entrypoint menjalankan agen sebagai user `lumbung` dalam loop yang menyalakannya lagi bila berhenti. Kegagalan agen tidak boleh menghalangi MariaDB start.
- Jadwal terakhir disimpan di `/backup/.lumbung/schedule.json` milik root. Data agen, termasuk daftar file terkirim, disimpan di `/backup/.lumbung/agent/` milik user `lumbung`. Keduanya berada di volume dan tidak dipindai sebagai backup.

Heartbeat, upload, dan pekerjaan backup berjalan di thread terpisah, karena full bisa berjalan sampai batas 1 jam dan tidak boleh terbaca sebagai agen mati. Upload dijalankan dengan `nice` dan `ionice` karena kini memakai jatah CPU dan disk yang sama dengan MariaDB.

Image perlu perubahan berikut:

- **Full dan dump menulis ke `.tmp` lalu rename,** seperti yang sudah dilakukan script binlog. Saat ini keduanya menulis langsung ke nama akhir, jadi file yang masih ditulis bisa terkirim bila diam lebih lama dari `STABLE_SECONDS`, dan file yang kemudian gagal bisa terlanjur ada di NAS.
- **Binlog lokal hanya dihapus bila sudah terkirim.** Full yang berhasil menghapus arsip binlog yang lebih tua dari posisinya, tanpa tahu apakah arsip itu sudah sampai di LumbungDB. Saat upload macet, rantai di NAS bisa putus permanen. Backup tetap berjalan saat upload macet atau LumbungDB mati. Arsip yang belum terkirim ditahan paling lama `BINLOG_UNSENT_MAX_DAYS` (bawaan 3 hari), dihitung dari waktu ubah arsip. Sesudah itu arsip dihapus walau belum terkirim, rantai di NAS putus, dan penghapusannya dicatat sebagai PERINGATAN di log backup. `binlogbackup.sh` dan `databasebackup.sh` sama-sama menerapkan batas ini, supaya tetap berlaku saat salah satu jadwal mati. Nilai yang bukan bilangan bulat minimal 1 diganti bawaan. Compose referensi meneruskan variabel ini ke container.
- **"Sudah berjalan" punya kode keluar sendiri,** misalnya 75. Saat ini kondisi itu dan backup gagal sama-sama keluar dengan kode 1. `databasedump.sh` juga menerima nama satu database sebagai argumen opsional; nilai `_users` hanya membuat dump akun dan hak akses.
- **Full mencatat binlog awalnya.** `databasebackup.sh` menulis `BINLOG_START` ke `<waktu>.xbstream.gz.info` sebelum file full diberi nama akhir. Agen membacanya dan mengirimnya lewat `X-Binlog-Start`. File `.info` tidak dikirim sebagai backup.
- **`install_cron.sh` mengikuti urutan jadwal di bawah.** Saat ini script menulis crontab dari env atau bawaan tiap container start, jadi jadwal dari dashboard akan hilang saat restart. Script membaca `schedule.json` lebih dulu dan memvalidasinya, karena isinya dipakai sebagai root. Bila tidak valid, script memakai urutan berikutnya.
- **Compose referensi mengisi `hostname` dari `CONTAINER_HOSTNAME`.** `install_cron.sh` menurunkan jam dari `hostname`, yang tanpa ini berisi ID container dan berubah tiap container dibuat ulang. Namanya bukan `HOSTNAME`, karena compose mengutamakan variabel dari shell daripada `--env-file`. Di server yang meng-export `HOSTNAME`, semua container akan mendapat nama mesin, sehingga jam backup-nya sama.
- **Dockerfile dilengkapi.** Image memasang `python3` dan `sudo`, membuat user `lumbung`, menyalin agen dan `lumbung-helper`, dan memasang satu aturan sudoers.
- **`docker-entrypoint.sh` menjalankan agen** bila `LUMBUNG_URL` terisi, sebelum menyerahkan proses ke entrypoint MariaDB.

### Jadwal bawaan dan perubahan

Setelan bawaan mengikuti `install_cron.sh`. Jam full dan dump diturunkan dari nama container, supaya container di satu server tidak backup bersamaan.

| Jenis | Bawaan | Script |
| --- | --- | --- |
| Full | Tiap hari, antara 00:00 dan 01:59 | `databasebackup.sh` |
| Dump tiap database dan `_users` | Tiap hari, antara 03:00 dan 04:59 | `databasedump.sh` |
| Arsip binlog | Tiap 15 menit | `binlogbackup.sh` |

Jadwal yang berlaku dipilih dengan urutan berikut:

1. Jadwal dari LumbungDB yang tersimpan di `/backup/.lumbung/schedule.json`.
2. `BACKUP_CRON`, `DUMP_CRON`, dan `BINLOG_CRON` di env container.
3. Jadwal bawaan dari nama container.

Alur perubahan:

1. Admin mengubah jadwal di dashboard: harian, mingguan, tiap N jam, kustom (cron 5 kolom), atau mati. Tiap perubahan menaikkan versi dan masuk `control_log`.
2. Dashboard menampilkan "Menunggu diterapkan". Agen menerima jadwal pada jawaban heartbeat berikutnya, paling lama sekitar 60 detik bila agen hidup.
3. Agen memvalidasi jadwal, lalu memanggil helper yang memvalidasi ulang dan menulis crontab baru. Bila berhasil, file `schedule.json` diganti. Bila gagal, crontab lama tetap dipakai dan agen melaporkan errornya.
4. Heartbeat berikutnya melaporkan versi yang diterapkan, jadwal yang berlaku, dan waktu jalan berikutnya. Dashboard selalu menampilkan laporan ini, bukan nilai yang diminta.

Aturan jadwal:

- Saat LumbungDB mati, cron tetap berjalan dengan jadwal terakhir, juga setelah container restart.
- Tidak ada tombol kembali ke bawaan. Bila ingin memakai jam lain, termasuk jam semula, admin langsung mengubah jadwalnya.
- Bila database metadata LumbungDB hilang, jadwal di agen tidak ditimpa. LumbungDB mengambil jadwal yang dilaporkan agen sebagai nilai awal.
- Form memberi peringatan bila full berdekatan dengan full container lain di server yang sama, dan peringatan keras saat mematikan full atau binlog.
- Retensi lokal 30 hari di script dan `BINLOG_UNSENT_MAX_DAYS` tidak diatur dari dashboard di rancangan ini.

### Permintaan backup

Tombol Backup sekarang di samping Ubah jadwal, di tab *Backup & log*, menawarkan full, dump semua database, dump satu database atau `_users`, dan arsip binlog. Sebelum full, dialog menampilkan durasi dan ukuran full terakhir, karena full berjalan di mesin produksi. Tab *Backup & log* menampilkan jadwal, daftar permintaan, dan riwayat perubahan.

| Status | Arti |
| --- | --- |
| Menunggu | Belum diambil agen; masih bisa dibatalkan |
| Dibatalkan | Dibatalkan admin sebelum diambil agen |
| Berjalan | Script sedang berjalan |
| Mengirim | Script selesai, file sedang dikirim ke LumbungDB |
| Selesai | Script berhasil dan semua file hasilnya tersimpan di NAS |
| Gagal | Script gagal, atau file hasilnya gagal terkirim; log ditampilkan |
| Sudah berjalan | Backup jenis itu sedang berjalan dari cron; tidak dijalankan dua kali |
| Ditolak | Sisa disk `/backup` kurang dari 1,2× ukuran full terakhir |
| Kedaluwarsa | Tidak diambil agen dalam `BACKUP_REQUEST_TTL_MINUTES` (15 menit) |
| Tidak selesai | Container restart atau agen mati saat berjalan |

Aturan permintaan:

- Permintaan kedaluwarsa supaya full yang diminta saat agen mati tidak tiba-tiba berjalan di jam sibuk beberapa jam kemudian.
- Permintaan dijalankan paling banyak satu kali. Agen mencatat ID permintaan sebelum menjalankan script, dan yang tidak selesai tidak diulang otomatis.
- Satu container hanya boleh punya satu permintaan aktif per jenis.
- Permintaan ditolak saat NAS bermasalah, karena hasilnya tidak bisa sampai ke NAS.
- Upload hasil permintaan membawa ID permintaan. File diperlakukan sama dengan backup terjadwal, termasuk retensinya, dan dibedakan dengan label Manual di riwayat.
- Agen mendahulukan file hasil permintaan, dan tidak perlu menunggu `STABLE_SECONDS` karena script sudah selesai.

### Perpindahan dari tag lama `zmutclik/mariadb`

Tag lama `zmutclik/mariadb`, seperti `12.3` dan `latest`, dan sidecar tidak dipakai lagi. Container lama tidak bisa diatur dari LumbungDB sampai dipindahkan.

Untuk tiap container:

1. Daftarkan lewat Tambah container, atau pakai token yang sudah ada bila container sudah terdaftar.
2. Di luar jam sibuk, isi `LUMBUNG_*` di `.env` dan isi `IMAGE_TAG` dengan versi MariaDB yang sedang berjalan, misalnya `11.4-a1`, bukan nilai contoh dari Tambah container. Isi `CONTAINER_HOSTNAME` dengan hasil `docker exec <container> hostname`, supaya jam jadwal bawaannya tetap. Jalankan compose dengan nama project lama pada `-p`, supaya volume data dan `/backup` yang lama tetap terpakai. MariaDB ikut restart.
3. Bila pernah memasang sidecar untuk uji coba, hentikan dan hapus.
4. Pastikan heartbeat melaporkan jadwal yang sama dengan sebelumnya, lalu tekan Backup sekarang untuk memastikan alurnya berjalan.

Daftar file terkirim belum ada, jadi agen memeriksa ulang semua file dan LumbungDB menjawab `exists` untuk yang isinya sama. Pakai `MAX_FILE_AGE_DAYS` untuk membatasi beban baca ini.

### Batas agen di dalam image

- **Pembaruan agen berarti restart MariaDB,** karena agen bagian dari image. Karena itu protokolnya harus tetap cocok dengan agen versi lama, dashboard menampilkan versi agen, dan pembaruan mengikuti jadwal pemeliharaan. Agen tidak memperbarui dirinya sendiri.
- **Bila MariaDB gagal start, agen ikut berhenti.** File yang belum terkirim dikirim dengan `docker run --rm` memakai image yang sama, volume `/backup` read-only, dan perintah `lumbung-agent --drain`. Runbook-nya perlu tersedia di luar aplikasi.

Selesai bila: satu container baru dan satu container lama memakai `zmutclik/mariadb:lumbungdb-*` lewat compose referensi, dan data container lama tetap utuh; agen berjalan sebagai `lumbung`, tidak bisa membaca `/etc/db_backup.cnf`, dan helper menolak aksi di luar daftar; perubahan jadwal di dashboard terlihat di `crontab -l` dan bertahan setelah restart; jadwal berisi teks tidak valid ditolak agen dan crontab lama tetap berjalan; backup terjadwal tetap jalan saat LumbungDB dimatikan; ketiga jenis permintaan berakhir Selesai dengan file di NAS; permintaan saat agen mati menjadi Kedaluwarsa dan tidak berjalan saat agen hidup lagi; container yang di-restart saat full berjalan berakhir Tidak selesai tanpa diulang; dan `lumbung-agent --drain` mengirim file saat MariaDB mati.

## Rancangan tahap 2: runner, uji restore, dan pemulihan

Uji restore membuktikan backup bisa dipulihkan, sedangkan database cadangan menyiapkan salinan untuk kejadian tidak terduga. Keduanya tidak menyentuh database produksi.

| | Uji restore | Database cadangan |
| --- | --- | --- |
| Tujuan | Membuktikan backup bisa dipulihkan | Siap dipakai saat downtime |
| Berjalan | Terjadwal per container, atau manual | Terjadwal untuk container yang diaktifkan, atau lewat Restore sekarang |
| Dikerjakan | Runner uji | Runner cadangan |
| Hasil | Container sementara, lalu dihapus | Tetap hidup di mesin runner |
| Tampil di | Tab *Uji restore* | Tab *Cadangan* |

### Runner dan tugas

- Tiap container punya dua penetapan: runner uji dan runner cadangan. Keduanya boleh sama atau berbeda, jadi uji bisa berjalan di mesin yang ringan dan database cadangan di mesin lain yang lebih siap dipakai saat downtime. Container baru memakai runner bawaan untuk keduanya. Runner uji diganti di tab *Uji restore*, runner cadangan di tab *Cadangan*, juga sebelum database cadangan diaktifkan. Ringkasan container menampilkan keduanya.
- Masalah pada satu runner hanya menghentikan tugas yang ia tangani. Bila runner uji bermasalah, database cadangan di runner lain tetap di-restore, dan sebaliknya.
- Tiap runner mengerjakan satu tugas. Bila waktunya bersamaan, tugas antre. `RUNNER_MAX_TOTAL` membatasi total supaya NAS dan VM LumbungDB tidak terbebani.
- Tugas punya batas waktu yang diperpanjang lewat laporan langkah. Bila runner mati di tengah tugas, tugas ditandai tidak selesai; saat hidup lagi, runner membersihkan sisa container dan scratch.
- Ganti runner uji berlaku mulai uji berikutnya, karena container sementara dihapus sesudah tiap uji.
- Saat pindah runner cadangan, runner baru memulihkan dan menguji dulu. Database cadangan di runner lama baru dihapus setelah lolos.
- Menu Runner menampilkan status, heartbeat, sisa scratch, tugas berjalan, container yang ditangani beserta tugas dan jadwalnya, dan riwayat tugas.

Hasil tugas ada tiga, dan "tidak terlaksana" sengaja dibedakan karena backup-nya bisa saja sehat:

| Hasil | Contoh | Dampak |
| --- | --- | --- |
| Lolos | Semua langkah berhasil | Menjadi bukti untuk retensi dan Restore sekarang |
| Gagal | File rusak, `--prepare` gagal, tabel tidak terbaca | Alert "Uji restore gagal" |
| Tidak terlaksana | Unduhan gagal, scratch penuh, runner mati | Alert runner; backup tidak dicap gagal dan retensi tidak menghitungnya sebagai lolos |

### Akun runner di hasil restore

Hasil restore dari full membawa akun produksi, dan runner tidak mengetahui password-nya. Karena itu runner memakai akun sementaranya sendiri untuk memeriksa tabel dan menerapkan binlog.

1. Sesudah `--prepare`, runner menjalankan MariaDB satu kali dengan `--skip-grant-tables`, `--skip-networking`, dan event scheduler mati.
2. Runner menjalankan `FLUSH PRIVILEGES`, lalu membuat `'lumbung_runner'@'localhost'` dengan password acak per volume, jadi akun ini hanya bisa masuk dari dalam container.
3. Password disimpan di disk scratch dengan izin 600, dan tidak pernah dikirim ke LumbungDB.
4. Runner mematikan MariaDB itu, lalu menjalankannya dengan pengaturan normal uji atau database cadangan.

Akun ini setara admin, karena penerapan binlog bisa berisi perubahan apa pun dan harus bisa menulis saat `read_only` aktif. Akun ini dihapus sebelum hasil restore menjadi production, baik lewat Naikkan ke production maupun Restore manual, supaya tidak ikut masuk backup berikutnya. Script Restore manual memakai cara yang sama. Uji restore tidak perlu menghapusnya, karena container sementara ikut dihapus. Impor dump memakai container kosong dengan password root buatan runner, jadi tidak memerlukan akun ini.

### Uji restore terjadwal

Tiap container punya jadwal sendiri, misalnya tiap Minggu 03:30, bisa dimatikan, dan cakupannya bisa dipilih: full, serta database tertentu. Dump `_users` dan rantai binlog selalu diperiksa. Admin juga bisa menjalankannya manual dengan cakupan yang sama. Hasil uji tidak mengubah database cadangan.

Tampilan menyebut yang tidak diuji, misalnya "tidak diuji: farmasi_stok" dan "full tidak diuji". Bila jadwal dimatikan, tab *Uji restore* memberi peringatan. Uji yang hanya mencakup dump bukan bukti untuk full.

| Jenis | Yang diambil | Cara uji | Lolos bila |
| --- | --- | --- | --- |
| Full | Full backup terbaru | Ekstrak xbstream, `mariadb-backup --prepare`, jalankan container MariaDB sementara dengan datadir hasil prepare | MariaDB naik, semua database terbuka, tabel utama terbaca (`SELECT COUNT(*)`) |
| Dump | Dump terbaru tiap database yang dipilih | Impor `.sql.gz` ke container MariaDB kosong, satu database per impor | Impor selesai tanpa error, jumlah tabel sama dengan database asal di hasil uji full, tabel utama terbaca |
| `_users` | Dump `_users` terbaru | Impor `.sql.gz` ke container MariaDB kosong | Impor selesai tanpa error dan akun hasil impor terbaca; tidak ada pembanding jumlah tabel |
| Binlog | Binlog dengan `chain_id` yang sama, mulai dari `binlog_start` full terakhir yang lolos uji | Cek metadata saja: nomor file berurutan tanpa lompatan dan tiap file lolos checksum | Rantai lengkap sampai binlog terbaru yang diterima |

Urutan satu uji:

1. **Full:** unduh file dari LumbungDB lewat HTTPS, cek SHA256 cocok dengan metadata di database, ekstrak xbstream dan `mariadb-backup --prepare`, jalankan container sementara, pastikan database terbuka dan hitung tabel tiap database, lalu pastikan tabel utama terbaca. Bila satu langkah gagal, sisa langkah full dilewati.
2. **Dump:** untuk tiap database yang dipilih, unduh dump terbaru, cek SHA256, impor ke container kosong, lalu cek tabel. Bila full gagal atau tidak diuji, pembanding jumlah tabel tidak tersedia dan yang dicek hanya tabel utama. `_users` diuji sesudahnya dengan caranya sendiri.
3. **Binlog:** pada rantai full terakhir yang lolos uji, mulai dari `binlog_start`-nya, cek nomor file berurutan dan checksum tiap file; tidak memerlukan container.
4. **Penutup:** hapus container sementara dan data ekstrak. Langkah ini selalu dijalankan, juga saat uji gagal.

Full yang gagal tidak menghentikan uji dump dan binlog, supaya tetap diketahui jalur restore mana yang masih bisa dipakai.

Hasil dan tindak lanjut:

- Hasil per jenis disimpan di tabel `restore_tests` (container, runner, waktu, cakupan, jenis, file, durasi, hasil, langkah gagal, log).
- Satu jenis gagal membuat uji container itu gagal dan memicu alert "Uji restore gagal" dengan aturan notifikasi yang sama dengan alert lain: satu pesan, pengingat 12 jam, pesan "Pulih" saat uji berikutnya lolos.
- Retensi tidak menghapus full terakhir yang lolos uji, dump terakhir yang lolos tiap database, dan binlog serantai dengan full terakhir yang lolos uji mulai dari `binlog_start`-nya, walaupun sudah lebih dari 3 hari, supaya full itu tetap bisa dipulihkan sampai titik waktu terbaru.
- Dashboard menampilkan uji restore terakhir di Ringkasan, riwayat langkah di tab *Uji restore*, dan rekap semua container di Laporan.

### Database cadangan terjadwal

Database cadangan terjadwal hanya untuk container yang diaktifkan. Jadwalnya ada dua: pemulihan full, misalnya tiap hari 03:00, dan penerapan binlog baru, misalnya tiap 1 jam.

1. Runner mengunduh full terbaru dan binlog miliknya, memulihkannya ke volume baru, lalu menerapkan binlog.
2. Runner memeriksa hasilnya: database terbuka dan tabel utama terbaca.
3. Bila lolos, volume itu menjadi database cadangan baru dan volume lama dihapus. Bila gagal, database cadangan lama tetap dipakai.
4. Di antara pemulihan full, binlog baru dari rantai yang sama diterapkan sesuai jadwal, melanjutkan posisi terakhir yang dicatat runner. Bila rantainya berganti, penerapan berhenti dan database cadangan menunggu pemulihan full berikutnya.

Aturan database cadangan:

- Berjalan `read_only`, portnya hanya untuk admin, dan versi MariaDB-nya sama dengan sumber.
- Event scheduler mati, karena image memasang `event_scheduler=ON`. Event yang berjalan di salinan bisa membuat datanya berbeda dari sumber, dan perubahan dari event produksi sudah datang lewat binlog.
- Tab *Cadangan* menampilkan jadwal, data sampai kapan, full yang dipakai, lokasi, dan riwayat restore.
- Restore yang lolos ikut menjadi bukti untuk full dan penerapan binlog.
- Mematikan jadwal menghapus database cadangan dari mesin runner, dan dicatat.
- Butuh ruang di mesin runner sebesar data asli, ditambah ruang yang sama saat database cadangan baru disiapkan.

Database cadangan selalu tertinggal dari produksi, karena binlog baru bisa diterapkan setelah terkirim ke NAS. Bila dibutuhkan selisih hitungan detik, jawabannya replika MariaDB, yang berada di luar lingkup ini. Replika ikut menyalin `DELETE` yang salah, sedangkan database cadangan dari backup masih punya salinan sebelum kesalahan itu, jadi keduanya saling melengkapi.

### Restore sekarang

Tombol Restore sekarang di tab *Cadangan* menjalankan pemulihan saat itu juga di runner cadangan, tanpa menunggu jadwal, dan juga tersedia untuk container yang jadwal restore-nya mati. Tombol ini tidak pernah me-restore ke database produksi.

- Admin memilih full: full terakhir yang lolos uji, atau full terbaru yang belum diuji dengan peringatan. Full yang gagal uji tidak ditawarkan. Binlog diterapkan sampai data terbaru yang tersedia. Pemulihan ke waktu tertentu atau satu database belum tersedia di dashboard (lihat Restore manual).
- Langkahnya sama dengan restore: pulihkan ke volume baru, periksa, lalu ganti database cadangan lama. Bila gagal, database cadangan lama tetap dipakai.
- Tidak bisa dijalankan bila tidak ada runner sehat di server lain, atau saat NAS bermasalah.
- Tanpa jadwal, hasilnya tidak di-restore otomatis. Menyimpan jadwal yang tetap mati lewat Ubah jadwal menghapusnya dari mesin runner.
- Tiap Restore sekarang dicatat di `restore_log` dan berlabel Manual di riwayat restore.

### Naikkan ke production

**Naikkan ke production** adalah jalur cepat saat downtime. Container cadangan keluar dari siklus restore dan menjadi container production di server tempatnya berjalan, jadi tidak perlu kunci atau langkah melepasnya. Mesinnya tetap runner, dan tetap menangani container lain. Aksi ini tidak bisa dibatalkan.

1. Dashboard menampilkan data sampai kapan, runner yang akan dipakai, dan meminta nama container baru, misalnya `simrs-utama-2`. Token dan folder NAS-nya terpisah, supaya file tidak tercampur bila container lama hidup lagi. Bila `agents.conf` gagal ditulis, aksi ditolak dan tidak ada yang berubah.
2. Runner membuat user backup baru dengan password acak dan hak yang sama dengan `init-backup-user.sh`, karena password user lama tidak disimpan LumbungDB, lalu menghapus `lumbung_runner`. Runner membuat ulang container pada volume yang sama dengan `.env` baru berizin 600: `LUMBUNG_*` dengan token dari tugas ini, `BACKUP_USER`, `BACKUP_PASS`, dan `*_CRON` kosong. `read_only` mati, event scheduler menyala, dan agen berjalan. MariaDB ikut restart. Bila langkah ini gagal, database cadangan tetap `read_only`, aksi ditandai gagal, dan alasannya tampil di dashboard.
3. Full backup langsung dijalankan; bila NAS bermasalah, agen mengulang kirim sampai NAS pulih. Uji dan database cadangan container baru memakai runner di server lain. LumbungDB memilih runner yang sehat; bila tidak ada, runner bermasalah di server lain tetap ditetapkan dan alert runner-nya menjelaskan dampaknya. Bila tidak ada runner di server lain sama sekali, aksi tetap berjalan karena ini jalur darurat, dan alert aktif sampai runner ditetapkan.
4. Admin membuka port, mengarahkan aplikasi, dan memastikan container lama tidak lagi menerima tulisan. Selisih data sesudah "data sampai" dipindahkan manual bila disk lama masih terbaca.

Pemindahan ke mesin lain tidak termasuk di sini, karena butuh salin data; itu bagian dari Restore manual.

### Larangan per container

Uji restore dan database cadangan sebuah container tidak boleh berjalan di server yang sama dengan container itu, baik terjadwal maupun manual, supaya production dan salinannya tidak hilang bersamaan saat server itu mati.

- Larangan dinilai per pasangan container dan runner. Runner yang satu server dengan `simrs-utama-2` tidak bisa menguji `simrs-utama-2`, tetapi tetap boleh menangani container lain.
- Server dibandingkan memakai `machine-id` dari heartbeat, jadi mengganti nama server tidak membuat server baru dan salah ketik tidak mungkin terjadi. Nama dari `/etc/hostname` hanya untuk tampilan.
- Bila ternyata satu server, larangan berlaku saat heartbeat pertama masuk. Dashboard menampilkan alasannya dan memberi alert sampai container memakai runner lain.
- VM hasil clone bisa punya `machine-id` yang sama. Bila satu `machine-id` muncul dengan dua nama server, dashboard memberi alert.

### Restore ke server lain

Restore sekarang punya pilihan **Restore di**, berisi runner yang sehat di server selain server container itu. Untuk restore ke server lain, pasang runner di server itu, lalu pilih runner tersebut. Setelah hasilnya lolos, runner itu menjadi runner cadangan, dan database cadangan di runner lama dihapus. Bila gagal, yang lama tetap dipakai. Untuk memindahkan production ke server itu, lanjutkan dengan Naikkan ke production.

### Restore manual

Tab *Restore manual* di detail container memulihkan seluruh container itu ke server mana pun dengan satu perintah, tanpa runner. Ini jalur untuk kejadian saat Naikkan ke production tidak bisa dipakai: tidak ada runner, database cadangan tidak disiapkan, atau production perlu dipulihkan di server baru.

```text
curl -fsSL https://lumbung.rumahsakit.co.id/restore.sh | RESTORE_TOKEN=lmr_xxxxxxxx sh
```

Alurnya:

1. Admin membuka tab *Restore manual* pada container sumber, termasuk yang nonaktif, lalu memilih full mana pun yang tersimpan. Tiap pilihan menunjukkan buktinya: lolos uji, belum diuji, atau gagal uji. Pilihan awalnya full terbaru yang lolos.
2. Admin memilih binlog: diterapkan sampai data terbaru, atau full saja untuk kembali ke sebelum kesalahan data. Admin juga mengisi nama container baru, port, dan batas waktu.
3. LumbungDB membuat sesi dan menampilkan perintah sekali. Admin menjalankannya di server tujuan.
4. Script memeriksa Docker, ruang disk, dan port, mengunduh file lewat HTTPS, memulihkan ke volume baru, lalu menjalankan MariaDB dengan `read_only` dan tanpa event scheduler untuk diperiksa. Tiap langkah tampil di dashboard.
5. Bila lolos, script membuat user backup baru dengan password acak di `.env`, menghapus `lumbung_runner`, membuka tulisan, menyalakan event scheduler dan agen, lalu mendaftar. Container baru langsung muncul di menu Container, dan full pertamanya dijalankan.

Admin tidak perlu mengisi nama server, image, atau password. Server terbaca dari mesinnya, dan tag image mengikuti sumber supaya MariaDB tidak naik versi. User backup baru dibuat karena password user lama tidak disimpan LumbungDB.

Aturan sesi:

- Batas waktu dipilih 2, 4, 8, atau 24 jam, dan berlaku sampai pendaftaran selesai. Selama aktif bisa diperpanjang 2 jam, paling lama `MANUAL_RESTORE_MAX_HOURS` sejak dibuat.
- Saat batas habis atau sesi dicabut, token mati, unduhan dan pendaftaran ditolak, dan script menghapus unduhan serta container sementara.
- Bila script gagal, perintah yang sama bisa dijalankan ulang di mesin yang sama sebelum batas waktu. Unduhan dilanjutkan. Bila gagal saat mendaftar, misalnya `agents.conf` tidak bisa ditulis, database yang sudah hidup tidak dipulihkan ulang.
- Nama container baru dicadangkan selama sesi aktif. Token, nama, dan folder NAS-nya terpisah dari sumber.
- File sesi yang masih aktif dilindungi dari retensi. Tidak bisa dibuat saat NAS bermasalah, dan unduhan yang berjalan menunggu NAS pulih.
- Sumber tidak disentuh, jadi admin tetap memastikan container lama tidak lagi menerima tulisan. Form memberi peringatan bila sumber masih mengirim heartbeat, dan dashboard memberi peringatan bila server tujuan sama dengan server sumber. Membuka port dan memindahkan aplikasi tetap dikerjakan admin.
- Container hasilnya memakai runner di server lain bila ada. Bila tidak ada, alert aktif sampai runner ditetapkan, karena backup-nya belum terbukti bisa dipulihkan.
- Tiap pembuatan, perpanjangan, pencabutan, dan hasil dicatat di `restore_log`, dan tiap unduhan dicatat.

Restore manual butuh LumbungDB yang hidup. Bila LumbungDB mati, atau perlu pemulihan ke waktu tertentu, satu database, atau akun `_users`, admin memakai file di NAS mengikuti README image, lewat izin NFS read-only sementara untuk server tujuan. Pemulihan ke waktu tertentu dari tab ini belum dirancang.

### Keputusan yang masih terbuka

- Container mana yang perlu database cadangan, dan seberapa sering binlog-nya diterapkan. Jawaban ini menentukan ukuran disk dan jumlah runner.
- Retensi untuk database yang tidak diuji, karena belum ada dump yang lolos uji untuk dilindungi.
- Penentuan "tabel utama": daftar per container di konfigurasi, atau otomatis N tabel terbesar dari hasil hitung.
- Nilai batas waktu per tugas.

Selesai bila: tiap container nyata yang dijadwalkan punya minimal satu uji yang lolos; satu container nyata punya database cadangan yang di-restore terjadwal, dan hasil penerapan binlognya cocok dengan sumber; satu backup yang sengaja dirusak (mis. dipotong) terdeteksi gagal dan memicu alert; unduhan yang sengaja diputus berlanjut tanpa mengulang dari awal; runner tidak bisa mengunduh file di luar tugasnya; container sementara tidak tertinggal setelah uji gagal; full lama hanya memakai binlog dari rantainya setelah nomor binlog kembali kecil; database cadangan tidak menjalankan event; dan container hasil Naikkan ke production menyelesaikan full pertamanya tanpa akun `lumbung_runner`.

## Rancangan tahap 3: kesehatan database

Agen akan membaca metrik langsung dari MariaDB lewat satu user khusus read-only, lalu mengirimnya ke LumbungDB bersama heartbeat; LumbungDB menyimpan deret waktunya dan membuat laporan harian serta mingguan. Hak akses persis user itu ditentukan saat implementasi, dengan prinsip hak minimum; metrik di bawah tidak memerlukan hak `PROCESS`.

| Kelompok | Metrik | Sumber |
| --- | --- | --- |
| Beban | Koneksi aktif dan puncak dibanding `max_connections`, koneksi ditolak | `Threads_connected` dan `Connection_errors_max_connections` di `SHOW GLOBAL STATUS`, `SHOW GLOBAL VARIABLES` |
| Performa | Hit ratio buffer pool InnoDB, jumlah slow query, deadlock | `Innodb_buffer_pool_read_requests`, `Innodb_buffer_pool_reads`, `Slow_queries`, dan `Innodb_deadlocks` di `SHOW GLOBAL STATUS` |
| Query terberat | Pola query, jumlah eksekusi, rata-rata durasi, baris diperiksa | `performance_schema.events_statements_summary_by_digest`, bila aktif |
| Ukuran | Ukuran tiap database dan tabel terbesar, pertumbuhan mingguan | `information_schema.TABLES` |
| Disk | Sisa ruang disk data dan `/backup`, perkiraan kapan penuh | Heartbeat agen, regresi linear atas riwayat |
| Backup | Umur dan ukuran backup terakhir dibanding rata-rata, kelengkapan binlog | Tabel `backups` |

Prinsip rancangan:

- Pengambilan ukuran tabel cukup harian karena berat. Koneksi dicuplik tiap 60 detik dan disimpan per jam beserta nilai dan waktu puncaknya, karena cuplikan per jam bisa melewatkan puncak. Angka ini usulan awal, belum diukur pada database sebenarnya.
- Penghitung di `SHOW GLOBAL STATUS` bersifat kumulatif, jadi nilai harian dihitung dari selisihnya, dengan penanganan saat MariaDB restart.
- `performance_schema` nonaktif secara bawaan di MariaDB dan butuh restart untuk diaktifkan. Bila nonaktif, hanya daftar query terberat yang tidak tersedia. Dampak bebannya perlu diukur di staging lebih dulu.
- Metrik disimpan di tabel baru `metrics` (container, waktu, nama, nilai) agar tren bisa dibuat tanpa menyimpan data isi tabel.
- Perkiraan disk penuh memakai regresi biasa, bukan AI.
- Laporan harian hanya memuat yang bermasalah; laporan mingguan memuat tren. Keduanya tampil di dashboard dan ringkasannya dikirim ke Telegram.
- Laporan disimpan sebagai snapshot saat terbit dan tidak berubah sesudahnya. Kejadian setelah itu masuk laporan berikutnya.
- Tiap container dalam laporan periode hanya dihitung sejak tanggal terdaftar.

Keputusan implementasi (3 November 2026):

- Agen membaca metrik lewat user DB `lumbung` (`IDENTIFIED VIA unix_socket`, tanpa password) dengan hak `SHOW VIEW` pada `*.*` dan `SELECT` pada `performance_schema.events_statements_summary_by_digest`. `SHOW VIEW` cukup agar ukuran tabel terlihat di `information_schema.TABLES` tanpa hak baca isi. Volume baru membuatnya lewat `init-backup-user.sh`; volume lama menjalankan SQL di `mariadb/README.md` sekali. Tanpa user itu heartbeat tetap jalan dan laporan menandai "tidak ada metrik".
- Cuplikan koneksi tiap heartbeat (60 detik) disimpan per jam sebagai nilai terakhir, puncak, dan waktu puncak. Penghitung disimpan sebagai jumlah selisih per jam. Ukuran database dan sepuluh tabel terbesar dikirim sehari sekali.
- Disk data belum diukur: user `lumbung` tidak boleh menyentuh datadir, jadi hanya sisa ruang `/backup` yang dipantau dan diperkirakan (regresi linear atas 14 hari, alert bila kurang dari `REPORT_DISK_BAD_DAYS`).
- Laporan terbit di worker pada `REPORT_HOUR` zona `DISPLAY_TIMEZONE` (harian tiap hari, mingguan tiap Senin). Hanya periode terbaru yang diterbitkan; periode yang terlewat karena worker mati tidak diisi ulang.
- Daftar query terberat dari `performance_schema` disimpan di `heartbeats.info`, belum dimasukkan ke laporan; itu bagian tahap 4.

Selesai bila: dua container nyata mengirim metrik minimal tujuh hari, laporan harian dan mingguan terbit otomatis, dan satu skenario sengaja (mis. backup dihentikan atau disk diisi) memicu peringatan yang benar.

## Rancangan tahap 4: lapisan AI

AI hanya menafsirkan laporan; semua angka dan alert tetap dihitung dengan aturan biasa. Peringatan backup gagal tidak boleh bergantung pada tebakan model.

Fitur yang direncanakan, berurutan dari yang paling berguna:

1. **Ringkasan naratif** harian dan mingguan, misalnya menandai ukuran backup yang turun jauh dari biasanya.
2. **Analisis slow query**: membaca pola query dari `performance_schema` dan menyarankan index atau penulisan ulang. Pola tanpa literal tidak bisa langsung di-`EXPLAIN`, dan `EXPLAIN` butuh hak baca tabel, jadi penggunaannya masih keputusan terbuka.
3. **Penjelasan anomali dan log error**: dari log mentah menjadi apa yang terjadi, seberapa penting, dan langkah berikutnya.
4. **Tanya-jawab** atas data laporan, mis. database mana yang tumbuh paling cepat bulan ini.

Batas privasi yang mengikat rancangan:

- Yang boleh dikirim ke model hanya metrik teragregasi dan metadata (ukuran, jumlah, durasi, nama tabel), tidak pernah isi data.
- Query dinormalisasi lebih dulu: nilai literal diganti `?`, karena nilai di `WHERE` bisa berupa nomor rekam medis atau nama pasien.
- Penyedia model dibuat sebagai modul yang bisa diganti. Pilihan cloud (mis. Claude API) atau lokal (mis. Ollama) mengikuti kebijakan rumah sakit; kualitas lokal lebih rendah dan butuh perangkat keras.

Pengaman operasional: di detail container ringkasan AI tampil sesudah tabel alert, keluaran AI diberi label "dibuat AI", disimpan bersama inputnya untuk audit, tidak bisa mengubah atau menekan alert, dan bila gagal laporan angka tetap terbit tanpa ringkasan.

Selesai bila: ringkasan harian terbit otomatis dari metrik nyata, normalisasi query terbukti membuang semua literal pada contoh uji, dan penyedia model bisa ditukar lewat konfigurasi tanpa mengubah kode lain.

## Pengujian

Skenario di bawah wajib lolos lebih dulu secara lokal, dengan file backup contoh bertanda waktu lama dan satu file baru yang sengaja dibuat "sedang ditulis".

| Skenario | Hasil yang diharapkan |
| --- | --- |
| NFS belum ter-mount (penanda tidak ada) | Upload ditolak 503, tidak ada file tertulis |
| Penanda ada, agen berjalan | Empat file terkirim: full backup, dump `siakdb`, dump `_users`, binlog |
| Agen dijalankan ulang | Tidak ada yang dikirim ulang |
| File yang masih baru (belum stabil) | Tidak dikirim |
| Token salah | 401 |
| Path `../` pada `databases` | 400 |
| `kind` tidak dikenal | 400 |
| Checksum salah | 422, tidak ada file `.part` tersisa |
| Dashboard tanpa dan dengan sesi login | Dialihkan ke halaman login, dan 200; `/api/status` tanpa sesi 401 |
| Login gagal melewati batas | 429 sampai masa penahanan habis, dan tercatat di `user_log` |
| Akun dinonaktifkan saat sesinya aktif | Permintaan berikutnya ditolak; aksi di riwayat tetap memakai nama akun itu |
| Aksi tulis tanpa token CSRF atau dengan `Origin` lain | 403 |
| Header `X-Forwarded-*` dari IP di luar `TRUSTED_PROXY_IPS` | Diabaikan; batas login memakai IP pengirim sebenarnya |
| `/api/status` | Menampilkan backup terakhir tiap jenis dan hitungan file; daftar alert kosong |

Sesudah itu, yang harus diuji di lingkungan nyata:

- Build image Docker dan `docker compose`.
- Container `zmutclik/mariadb:lumbungdb-*` sungguhan, termasuk cara script backup menulis file.
- NFS Synology sungguhan, termasuk pemetaan UID dan perilaku saat NAS mati.
- Pemicu alert (ambang waktu, disk, mount) dan pengiriman Telegram.
- File besar (full backup ukuran GB), upload bersamaan dari beberapa agen, dan upload yang terputus di tengah.
- Penyimpanan versi baru untuk file bernama sama dengan isi berbeda, termasuk nomor binlog yang kembali kecil, nama lokal `<nama>.<waktu>.gz`, perhitungan `chain_id`, dan `binlog_start`.
- Upload yang macet melewati `BINLOG_UNSENT_MAX_DAYS`: arsip ditahan sampai batas, lalu dihapus dan tercatat di log backup.
- Pembacaan waktu dari nama file asli, zona waktunya, dan alert dump per database.
- Pemeriksaan NAS terpisah saat NAS benar-benar macet: alert terkirim, dashboard dan `/api/watchdog` tetap menjawab, upload baru ditolak 503, dan apakah batas 10 detik memicu alarm palsu saat NAS sibuk.
- Beberapa agen di satu server, dan pengelompokan container per server dari heartbeat, termasuk mount `/etc/hostname` dan `/etc/machine-id`.
- Agen di dalam image sebagai user non-root, `lumbung-helper`, izin baca file di `/backup`, perubahan jadwal dari dashboard, dan permintaan backup. Perubahan script baru berdasarkan pembacaan isi script.
- Semua rancangan tahap 2: runner, akun `lumbung_runner`, unduhan lewat LumbungDB, uji restore terjadwal, database cadangan, penerapan binlog berkala, Restore sekarang, dan Naikkan ke production. Kodenya belum ada.

Prototype dashboard diuji di Chromium pada 1 November 2026: menu Container dan Runner, kedua wizard, jadwal, simulasi kondisi, dan layar 390px. Uji ini memakai data contoh, jadi hanya membuktikan tampilan dan alurnya, bukan restore sungguhan.

Bagian jadwal backup, permintaan backup, dan pemasangan lewat image di prototype juga diuji di Chromium pada 1 November 2026, termasuk cron tidak valid, jadwal ditolak agen, agen mati, semua hasil permintaan, container nonaktif, NAS bermasalah, login kosong, layar 390px, dan tema gelap. Uji ini juga memakai data dan agen simulasi, jadi belum membuktikan agen, crontab, dan script sungguhan.

Rencana: tambahkan tes otomatis (pytest) untuk endpoint upload dan fungsi `compute_alerts` dengan jam palsu, lalu satu uji manual end-to-end di lingkungan staging sebelum dipasang di produksi.

## Risiko dan keputusan terbuka

Risiko terbesar bukan di kode tetapi di asumsi yang belum dibuktikan dengan container, NAS, dan restore sungguhan.

| Risiko atau asumsi | Dampak | Penanganan |
| --- | --- | --- |
| Script full dan dump menulis langsung ke nama akhir; hanya binlog yang memakai `.tmp` (dari isi script, belum dicoba di container) | File yang belum selesai bisa terkirim terpotong, dan checksum tidak mendeteksinya karena dihitung dari file yang sudah terpotong | Jeda `STABLE_SECONDS` hanya mengurangi peluangnya; ubah script agar menulis `.tmp` lalu rename, sebaiknya sebelum produksi |
| Full yang berhasil menghapus arsip binlog lokal tanpa tahu apakah sudah terkirim | Saat upload macet, binlog yang belum sampai ke NAS hilang dan rantainya putus permanen | Script hanya menghapus binlog yang tercatat terkirim; alert upload macet. Selama macet, arsip ditahan paling lama `BINLOG_UNSENT_MAX_DAYS`, lalu dihapus dan dicatat di log, jadi macet yang lebih lama dari batas itu tetap memutus rantai |
| File bernama sama bisa berisi data berbeda, terutama binlog setelah nomornya kembali kecil, dan full lama bisa dipasangkan dengan binlog dari rantai lain | Bila hanya dibandingkan dari nama, LumbungDB menjawab `exists`, jadi file baru tidak tersimpan dan agen mengira sudah terkirim | Simpan sebagai versi baru, pisahkan lewat `chain_id` dan `binlog_start`, dan picu alert bila bukan rantai baru; wajib diterapkan sebelum produksi |
| Notifikasi Telegram gagal tidak diulang sampai 12 jam | Alert bisa terlambat | Tambah antrean kirim ulang |
| Satu LumbungDB dan satu database metadata | Pemantauan, uji, dan restore berhenti bila VM mati, dan alert tidak bisa melaporkan dirinya sendiri; backup di server tetap ada dan agen mengulang | Pemeriksa `/api/watchdog` dari luar VM, healthcheck container, backup volume `lumbung_state`, pemindaian ulang NAS |
| NFS `hard` mount bisa membuat proses menunggu tanpa batas saat NAS mati | Upload yang sedang menulis ke NAS bisa tersangkut sampai NAS kembali atau container di-restart | Pemeriksa NAS terpisah dan batas upload bersamaan menjaga alert dan dashboard tetap berjalan |
| Retensi baru ada di tahap 2 | NAS bisa penuh | Alert sisa ruang di tahap 1; retensi di tahap 2 |
| Waktu di nama file memakai `YYYYMMDD_HHMMSS` dengan zona Asia/Jakarta menurut Dockerfile dan script; belum dicoba di container | Umur backup bisa meleset beberapa jam bila zona container diubah, dan alert terlambat atau terlalu cepat | Verifikasi di staging dan tetapkan zona waktu per container bila berbeda; NTP di semua server |
| Jalur HTTP tanpa enkripsi | Token dan file bisa disadap di jaringan | HTTPS lewat reverse proxy sebelum produksi |
| Semua file runner melewati VM LumbungDB | Unduhan full besar bisa mengganggu upload dan dashboard; NAS yang macet bisa membuat unduhan tersangkut | Batas unduhan dan kecepatan, jadwal di luar jam upload, unduhan yang bisa dilanjutkan, pisahkan dari jalur alert |
| Mesin runner dibobol | Scratch dan database cadangan berisi data pasien asli, dan token runner bisa mengunduh file dari tugasnya | Tanpa akses NFS, akses per tugas, disk terenkripsi, cabut token, mesin khusus |
| Penerapan binlog berkala ke database cadangan yang hidup belum pernah dicoba | Database cadangan bisa tertinggal atau tidak cocok dengan sumber, dan baru ketahuan saat dibutuhkan | Buktikan dengan data asli, bandingkan hasil dengan sumber |
| Database cadangan selalu tertinggal dari produksi | Data terbaru sebelum downtime bisa tidak ikut | Tampilkan "data sampai", atur jadwal binlog; replika bila butuh hitungan detik |
| Kapasitas runner belum diukur | Disk tidak cukup, atau tugas antre melewati jadwal berikutnya | Ukur dari backup asli; tambah runner bila perlu |
| Database yang tidak diuji | Bisa rusak tanpa ketahuan, dan belum punya perlindungan retensi | Tampilkan yang tidak diuji, peringatan saat jadwal mati, tetapkan aturan retensinya |
| Container baru lupa didaftarkan | Tidak ada backup terpusat dan alert, karena LumbungDB tidak mendeteksi container tanpa agen | Jadikan pendaftaran bagian dari prosedur membuat container |
| Identitas server dari mount `/etc/machine-id` belum dicoba | File bisa tidak ada di sebagian server, atau sama di VM hasil clone, sehingga server salah dikelompokkan | Bila tidak terbaca, runner tidak diberi tugas dan container tampil di kelompok Belum terhubung; alert bila nama server berbeda dengan `machine-id` sama; coba di tiap jenis server |
| Naikkan ke production belum dicoba, dan runner mengubah database menjadi production | Salah tekan atau dashboard dibobol bisa memunculkan production kedua; data bisa terpecah bila container lama hidup lagi | Konfirmasi kuat, dicatat di `restore_log`, nama container baru, latih pada mesin uji |
| Akun `lumbung_runner` belum dicoba: `--skip-grant-tables`, hak menulis saat `read_only`, dan penerapan binlog | Database cadangan bisa gagal diperiksa atau diperbarui, dan akun setara admin bisa tertinggal di production bila penghapusannya gagal | Coba pada versi MariaDB yang dipakai, tetapkan hak persisnya, dan gagalkan aksi bila akun tidak bisa dihapus |
| Rantai binlog dihitung dari urutan waktu ubah file | Jam server yang salah, atau `/backup` yang disalin tanpa waktu ubahnya, bisa memecah atau menyambung rantai secara keliru | NTP di semua server, pertahankan waktu ubah saat menyalin, uji dengan penomoran yang kembali kecil dan file yang terlambat terkirim |
| Container production berjalan di mesin runner | Runner memegang akses Docker di mesin itu, dan tugas container lain memakai disk serta CPU yang sama | Pindahkan production ke server khusus setelah insiden selesai; pantau beban; batasi akses mesin runner |
| Script Restore manual belum ada dan belum dicoba, termasuk membuat user backup baru pada hasil full | Pemulihan tanpa runner bisa gagal saat darurat | Buat dan latih pada mesin uji, juga pada server yang baru dipasang |
| Script pasang dan script Restore manual dijalankan dengan akses Docker, dan isinya datang dari LumbungDB | Bila LumbungDB dibobol, admin yang menempelkan perintah bisa menjalankan script penyerang di server tujuan | HTTPS tepercaya, admin bisa membaca script lebih dulu, simpan checksum script di runbook di luar aplikasi |
| Pemulihan ke waktu tertentu dan satu database belum ada menunya | Bisa gagal saat darurat bila dikerjakan manual dari NAS | Tulis runbook dari README image dan latih pada mesin uji |
| Semua container lama harus dibuat ulang dengan image baru | Tiap container perlu restart, dan salah nama project bisa membuat volume kosong baru sehingga MariaDB tampak kehilangan data | Pindahkan satu per satu di luar jam sibuk, pastikan nama volume dan backup terakhir sebelum mulai, coba dulu di staging |
| Agen satu container dengan MariaDB | Agen memakai jatah CPU dan disk database, celah pada agen bisa membaca file backup dan memakai helper, dan pembaruannya butuh restart MariaDB | Agen non-root tanpa port, helper dengan daftar aksi tertutup, `nice` dan `ionice` saat upload, protokol tetap cocok dengan versi lama, ukur bebannya di staging |
| MariaDB gagal start saat masih ada file belum terkirim | Binlog dan file terakhir tertahan di server justru saat dibutuhkan | `lumbung-agent --drain` dari image yang sama; latih dan simpan runbook-nya |
| Semua akun admin punya hak yang sama, tanpa login dua langkah | Satu akun yang bocor bisa mengunduh seluruh data lewat Restore manual, menjalankan Naikkan ke production, dan membuat akun baru | Akun per orang, batas login gagal, sesi berumur terbatas, `user_log` dan `restore_log`, dashboard hanya dari jaringan tepercaya |
| LumbungDB dibobol, atau admin salah mengubah jadwal | Backup bisa dimatikan di banyak container, atau full berjalan di jam sibuk | Daftar aksi tertutup, `control_log`, alert jadwal full mati, label jadwal yang dimatikan, konfirmasi sebelum full |

Keputusan yang masih menunggu kamu:

- Saluran notifikasi final: Telegram dipakai sebagai bawaan; WhatsApp atau email bisa menjadi tambahan.
- Penyedia AI: cloud atau lokal, sesuai kebijakan rumah sakit tentang data keluar jaringan.
- Container mana yang perlu database cadangan, dan seberapa sering binlog-nya diterapkan.
- Retensi untuk database yang tidak diuji.
