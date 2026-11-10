# Status progres LumbungDB

Diperbarui 4 November 2026 (commit `e9001ae`). Rincian rancangan ada di `dokumen_pengembangan.md`; berkas ini hanya
merangkum **apa yang masih kurang** supaya mudah dilanjutkan.

## Sudah ada kodenya
Tahap 1 (pengumpulan backup, dashboard, alert), tahap 2 (runner, uji restore, database cadangan, Restore sekarang,
Naikkan ke production, Restore manual, retensi, antrean notifikasi, pindai ulang NAS), tahap 3 (metrik dan laporan), dan
tahap 4 (ringkasan laporan, penjelasan container, tanya-jawab, analisis slow query; mati bawaan, `AI_PROVIDER=none`).
465 tes pytest dan `scripts/e2e.sh` lolos. Simulasi Docker sungguhan: `scripts/sim/runner_real.py`, `standby_real.py`
(termasuk Naikkan ke production), `manual_real.py`.

## Yang masih kurang

### 1. Pembuktian di lingkungan nyata
Semua uji memakai folder lokal sebagai NAS, Telegram tiruan, dan data contoh. Belum diuji dengan:
- NFS Synology sungguhan (mount, izin, perilaku saat NFS macet).
- Telegram sungguhan (alert, laporan, antrean kirim ulang).
- Runner di mesin terpisah dari server container.
- Satu container MariaDB yang meniru produksi, dari Tambah container sampai backup masuk.
- Tujuh hari metrik nyata (gerbang 3, laporan andal).
- Latihan penuh: restore, Naikkan ke production, dan Restore manual pada container yang meniru produksi, termasuk satu
  pemulihan dengan kunci pemulihan age.
- Penyedia AI: ringkasan dari metrik nyata, dan API Claude atau Ollama asli (baru satu gateway OpenAI-compatible dicoba).

### 2. Audit keamanan
- Audit tahap 1 terhenti (`~/security-audit-skill/lumbungDb/run-1`): kandidat temuan belum divalidasi.
- Dikerjakan di kode akhir (keputusan 3 November 2026). Fitur sesudah tahap 1 belum pernah diaudit: runner, Restore manual
  (token dan script publik), Naikkan ke production, dan lapisan AI.

### 3. Keputusan yang menunggu pemilik
- Container mana yang perlu database cadangan, dan seberapa sering binlog diterapkan (menentukan disk dan jumlah runner).
- Retensi untuk database yang tidak diuji.
- Penentuan "tabel utama" untuk uji restore.
- Batas waktu per tugas runner.
- Penyedia AI: cloud atau lokal, sesuai kebijakan rumah sakit.

### 4. Syarat gerbang 1 yang tertulis tapi belum terbukti
- Reverse proxy dengan SSL beli, port LumbungDB hanya untuk proxy.
- Izin NFS dibatasi ke IP VM LumbungDB.
- Kunci privat age disimpan offline di dua tempat.
- Izin `.env` dan `config/*.conf` 600.
- Prosedur rotasi token (ada di README) belum pernah dilatih.

### 5. Celah yang diketahui
- `scripts/e2e.sh` belum mencakup runner, Naikkan ke production, Restore manual, dan AI.
- Image `zmutclik/mariadb:lumbungdb-*` baru dibangun lokal, belum di-push ke registry.
- Runner yang belum pernah mengirim heartbeat bisa terpilih sebagai runner container baru (alert runner yang menandai).
- Penaikan yang runner-nya mati di tengah (batas waktu habis) meninggalkan nama container baru terdaftar di
  `agents.conf` tanpa agen; pembersihan hanya jalan saat runner melapor gagal.
- Data production hasil penaikan ada di disk scratch runner (`PROMOTE_DIR`): butuh kebijakan disk dan cadangan sendiri.
- Ringkasan AI belum tampil di detail container seperti di rancangan, dan tidak dikirim ke Telegram.
- Runbook pemulihan di luar aplikasi (saat LumbungDB mati) belum tertulis.
- Saran AI bisa keliru (mis. index berlebihan); selalu uji di database cadangan.

## Urutan yang disarankan
1. Putuskan butir 3, karena menentukan konfigurasi.
2. Tulis daftar periksa dan runbook pembuktian (butir 1) serta skenario e2e untuk runner, penaikan, dan Restore manual.
3. Jalankan pembuktian di infrastruktur nyata.
4. Audit keamanan di kode akhir.

## Catatan kerja
- Tes server: container `lumbungtest-db` (lihat README) dan `TEST_DATABASE_URL=...lumbung_test`.
- Hapus container uji dengan `docker rm -f -v` supaya volume anonim tidak menumpuk: image `lumbungdb` membuat redo log
  sekitar 3 GB per container, dan disk host sempat penuh karena itu.
- Jangan menaruh kunci API di repo; `AI_API_KEY` hanya di `.env`.
