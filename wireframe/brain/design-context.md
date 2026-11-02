# Design Context — LumbungDB

Generated: 2026-10-30

## App Overview
Pusat pengumpul backup dan pemantau untuk container MariaDB (`zmutclik/mariadb:lumbungdb-*`) di rumah sakit. Agen di dalam tiap container mengirim file backup (full, dump per database, binlog) dan heartbeat ke server FastAPI, yang menyimpan file di NAS Synology (NFS) dan metadata di SQLite. Alert dikirim lewat Telegram. UI dirancang lewat prototype di `wireframe/prototype/`; dashboard ada di `GET /` (sesi login akun admin, refresh 60 detik).

## Target Platform
Desktop / Web (dashboard internal untuk admin IT; ditampilkan di monitor kantor atau laptop).

## Layout Patterns
- Belum ada kode UI. Default yang aman: halaman server-rendered sederhana, bar atas + konten satu kolom, tabel data.
- Data padat (admin/monitoring) → token Precision & Density dari design-taste.md.

## Navigation
- Belum ada. Kandidat menu (dari dokumen_pengembangan.md): Ringkasan, Alert, Server (detail), Riwayat backup, Pengaturan (read-only dari .env).
- Menu tahap berikutnya: Uji restore (tahap 2), Kesehatan DB dan Laporan (tahap 3), Analisis AI (tahap 4).

## Page Types
### Ringkasan
- Status NAS (mount, sisa ruang), jumlah alert, tabel/kartu per server.
- Key elements: umur full backup, umur dump, heartbeat terakhir, sisa disk /backup, status.

### Alert
- Daftar alert aktif: kondisi, server, sejak, notifikasi terakhir.

### Detail server
- Info heartbeat (nama server, disk, jumlah file per jenis), 20 baris log backup, daftar file yang diterima.

### Riwayat backup
- Daftar file dari tabel `backups` dengan filter server/jenis.

### Server (daftar + tambah) — wireframe 3010-tambah-server
- Semua agen dan token hanya di `config/agents.conf` (satu sumber). Ganti token dan nonaktifkan (`#` di awal baris) lewat file itu; dashboard tidak punya Ganti token/Aktifkan/Hapus. Detail server hanya punya menu ⋮ berisi Nonaktifkan (memberi `#` di baris agen; tidak menghapus).
- Token tidak pernah tampil di aplikasi, termasuk awalannya; hanya sekali di Tambah server (yang menambahkan baris ke agents.conf). Daftar dan detail server tanpa token.
- Tambah server: nama (huruf/angka/_.-, diawali huruf atau angka, nama project compose tidak boleh bentrok) → token tampil sekali + `.env` + perintah compose siap salin → status "Menunggu heartbeat pertama".
- Aksi per server: Nonaktifkan (file NAS tidak dihapus). Tidak ada Abaikan alert.

## Interaction Patterns
- Halaman refresh otomatis 60 detik; hampir semuanya read-only.
- Aksi tulis pertama: tambah server, nonaktifkan. Aksi berisiko memakai konfirmasi, menjelaskan dampaknya, dan fokus awalnya di tombol Batal. Semua aksi tulis dimatikan bila login dashboard kosong.
- Rahasia (token) hanya tampil sekali dengan peringatan tebal dan tombol Salin; setelah itu token tidak tampil sama sekali.

## Content Hierarchy
- Status bermasalah selalu di atas; server sehat boleh diringkas.
- Waktu memakai WIB dan umur relatif ("6 jam lalu").

## Screenshot Observations
- Tidak ada screenshot aplikasi asli; kode server dan agen tidak ada di folder ini.

## UX Conventions
- Bahasa antarmuka: Indonesia.
- Umur backup dihitung dari waktu pembuatan (nama file atau mtime), bukan waktu diterima.
- Status alert mengikuti 6 kondisi di dokumen: NAS tidak ter-mount, ruang NAS < 10%, heartbeat > 10 menit, full > 36 jam, dump > 36 jam per database, dump `_users` > 36 jam. Server baru tanpa backup = bermasalah. Tahap 2 menambah rantai binlog putus dan upload gagal.
- NAS yang macet tampil "tidak merespons", berbeda dari "tidak ter-mount"; alert dan dashboard tetap berjalan pada keduanya.
- Alert NAS berlaku untuk semua server: tampil sebagai banner di semua halaman dan baris alert dengan server "semua". Contoh tampilannya dipilih di Pengaturan › Simulasi prototype.

## Keputusan desain (30 Okt 2026)
- Dipilih: tahap 1 opsi 1 (Menu Atas, isi halaman), tahap 2 opsi 1 (Menu Terpisah), tahap 3 opsi 1 (Menu Terpisah), tambah server opsi 2 (Wizard 3 Langkah).
- Kerangka: satu bar atas gelap berisi logo, menu horizontal (Dashboard, Server, Laporan, Pengaturan), waktu refresh, dan user; sidebar kiri dihapus 31 Okt 2026 karena menunya sedikit. Breadcrumb hanya tampil di halaman turunan, di atas konten. Isi halaman tahap 1 tetap.
- Grup menu: Ringkasan, Alert · Server (daftar + tiap server) · Backup (Riwayat, Retensi, Uji restore) · Pemeliharaan (Integritas, Pindai ulang NAS, Antrean notifikasi) · Kesehatan (Kesehatan DB, Laporan) · Sistem (Pengaturan).
- Tab Kesehatan di detail server menampilkan semua metrik sekaligus dalam bagian Beban, Performa, Ukuran, Disk, tanpa sub-tab (31 Okt 2026). Tautan `kesehatan/<bagian>` menggulir ke bagian itu.
- Tema terang dan gelap (31 Okt 2026): tombol di bar atas, pilihan disimpan di `localStorage` (`lumbung-theme`), tanpa pilihan ikut pengaturan sistem. Semua warna lewat token CSS; token gelap hanya berlaku di layar, jadi cetakan laporan tetap terang.
- Pengaturan › Simulasi prototype menampilkan kondisi yang tidak ada di data utama: semua sehat, belum ada server, NAS bermasalah, AI gagal, `agents.conf` gagal ditulis, dan login kosong.
- Prototype selalu menampilkan seluruh fitur, tanpa label atau pilihan tahap. Pembagian tahap hanya ada di `dokumen_pengembangan.md`.
- Istilah dan menu (diubah 2 Nov 2026): Server (mesin) › Container (satu MariaDB, satu agen, satu token) › Database. Jangan memakai kata "host" karena ambigu. Runner adalah server atau VM khusus uji dan cadangan. Menu: Dashboard, Container, Runner, Laporan, Pengaturan. Rute: `#/container`, `#/container/<nama>/<tab>`, `#/runner/<nama>`. Daftar server disusun dari identitas server di heartbeat, yang dibaca agen dari mesinnya dan tidak pernah diisi manual; container yang belum mengirim heartbeat masuk kelompok "Belum terhubung".
- Runner (1 Nov 2026): program terpisah yang mengambil tugas dan mengunduh file dari LumbungDB lewat HTTPS, tanpa akses NFS, lalu menjalankan uji restore dan database cadangan; bisa di VM atau server mana pun. Token di `config/runners.conf`, tampil sekali di Tambah runner. Tiap container punya runner uji (diganti di tab Uji restore) dan runner cadangan (diganti di tab Cadangan), yang boleh berbeda. Hasil uji "Tidak terlaksana" (masalah di sisi runner) dibedakan dari "Gagal" (backup bermasalah). Full yang lolos menjadi database cadangan baru; bila gagal, database cadangan lama tetap dipakai.
- Uji restore dan database cadangan adalah dua jadwal terpisah per container (1 Nov 2026). Tab *Uji restore* (`#/container/<nama>/restore`): jadwal, misalnya mingguan, dan cakupan yang bisa dipilih (full dan/atau database tertentu); hasilnya tidak mengubah database cadangan. Tab *Cadangan* (`#/container/<nama>/pulih`): database cadangan hanya untuk container yang diaktifkan, dipulihkan terjadwal dari full lalu binlog, untuk kejadian tidak terduga atau downtime. Restore yang lolos ikut menjadi bukti full dan penerapan binlog. Alert database cadangan muncul bila melewati 2× jadwal.
- Database cadangan (diubah 2 Nov 2026): container hasil restore yang tetap hidup di mesin runner disebut "database cadangan" dan runner-nya "runner cadangan", karena sifatnya cadangan; jangan memakai "standby". Prosesnya disebut restore (Restore sekarang, restore terjadwal), bukan pembaruan. Nama container-nya `lumbung-cadangan-<nama>`; `lumbung-restore-*` khusus container sementara uji. Tab *Cadangan* berisi Database cadangan dan riwayat tindakan. "Naikkan ke production" menaikkan container cadangan menjadi container production dengan nama baru, dan tidak bisa dibatalkan; mesinnya tetap runner. Larangan per container: uji dan cadangan sebuah container tidak berjalan di server yang sama dengan container itu. Restore sekarang bisa memilih runner lain lewat "Restore di" untuk restore ke server lain. Wizard "Siapkan restore" dihapus; restore lewat LumbungDB hanya memulihkan seluruh container di runner cadangan. Menu restore manual menyusul, rancangannya belum ditentukan.
- Image dan kendali backup (1 Nov 2026): agen ada di dalam image `zmutclik/mariadb:lumbungdb-*`, satu-satunya image yang didukung, jadi jadwal semua container bisa diubah dari dashboard. `zmutclik/mariadb` dan sidecar tidak dipakai lagi. Tambah container memasang image lewat `docker-compose.lumbung.yml` dan `.env`, juga untuk memindahkan container lama. Tab *Backup & log* menampilkan Jadwal backup (jadwal yang dilaporkan agen, asal jadwal, waktu berikutnya, ambang alert 1,5× jarak jadwal, dan "Menunggu diterapkan"), Permintaan backup, dan Riwayat perubahan. Tombol Backup sekarang ada di samping Ubah jadwal. File hasil permintaan berlabel Manual di riwayat file. Container nonaktif hanya menampilkan jadwal terakhir. Hasil permintaan dan penolakan jadwal bisa dicoba di Pengaturan › Simulasi prototype.
- Restore sekarang (diubah 2 Nov 2026): tombol di bagian Database cadangan pada tab *Cadangan*. Memulihkan full yang dipilih dan binlog di runner cadangan, tidak pernah ke produksi; hasilnya berlabel Manual di riwayat.
- Restore manual (2 Nov 2026): tab di detail container, `#/container/<nama>/manual`, dan menggantikan catatan "menu restore manual menyusul" di atas. Bukan menu atas, dan jumlah sesi aktif tampil di label tab. Untuk pemulihan tanpa runner: pilih full, lalu satu perintah `curl … | RESTORE_TOKEN=… sh` di server tujuan. Hasil yang lolos langsung terdaftar sebagai container baru. Token tampil sekali dan hilang saat halaman sesi ditinggalkan; ada batas waktu, Perpanjang, dan Cabut. Tag image mengikuti sumber.
- Akun admin (2 Nov 2026): login per orang lewat sesi, semua akun punya hak yang sama, dan dikelola di Pengaturan › Akun admin. Rincian ada di `dokumen_pengembangan.md`; prototype belum menampilkannya.
- Prototype berwarna: `wireframe/prototype/` (token Precision & Density, aksen teal `#0e7490`, bar atas gelap `#0f1b2d`; status merah/kuning/hijau/biru untuk bermasalah/perhatian/OK/menunggu; ungu hanya untuk konten buatan AI).
