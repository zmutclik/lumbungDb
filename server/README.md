# LumbungDB: server

Berawal dari template skill `fullstack-architecture`. Cara memasang dan menguji ada di `../README.md`.

| Domain | Isi |
| --- | --- |
| `app/core` | Config, database, templating, error, middleware, log, health |
| `app/auth` | Akun admin, sesi, CSRF, batas login, `user_log`, CLI |
| `app/agents` | `agents.conf` dan token agen |
| `app/nas` | Pemeriksaan NAS lewat proses kecil dan `nas_status` |
| `app/containers` | Container dan heartbeat terakhir |
| `app/backups` | Upload, versi file, rantai binlog, tinjauan checksum |
| `app/backup_control` | Heartbeat, jadwal, cron, permintaan backup |
| `app/alerts` | Aturan alert dan worker pengecek |
| `app/integrations` | Adapter Telegram |
| `app/dashboard` | Semua halaman |

Arah dependensi: `agents`, `nas` ← `containers` ← `backups` ← `backup_control` ← `alerts` ← `dashboard`.
