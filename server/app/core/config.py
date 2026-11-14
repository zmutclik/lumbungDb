import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DEBUG: bool = False
    DATABASE_URL: str                    # mysql+asyncmy://user:pass@host:3306/db
    # Signing saja (token CSRF), bukan kunci enkripsi. Kosong/pendek ditolak saat start, bukan diam-diam dipakai.
    SECRET_KEY: str = Field(min_length=32)

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_STATEMENT_TIMEOUT: float = 2.5    # engine web (OLTP)

    HOME_PATH: str = "/dashboard"        # tujuan setelah login dan GET /
    DISPLAY_TIMEZONE: str = "Asia/Jakarta"   # hanya untuk tampilan (filter `localtime`); DB tetap UTC

    # TTL = batas mutlak: penyegaran otomatis dashboard tidak membuat sesi hidup terus, jadi monitor kantor login lagi.
    SESSION_TTL: int = 60 * 60 * 12
    SESSION_MAX_AGE: int = 60 * 60 * 12          # batas mutlak sejak login (juga max_age cookie)
    COOKIE_SECURE: bool = True
    LOGIN_MAX_FAILURES: int = 5          # per pasangan (username, IP)
    LOGIN_MAX_FAILURES_PER_IP: int = 50  # per IP untuk semua username; longgar agar kantor di balik NAT tidak terblokir
    LOGIN_FAILURE_WINDOW: int = 15 * 60
    PASSWORD_HASH_CONCURRENCY: int = 4   # argon2 paralel per proses; tiap hash ±64 MiB RAM

    # Agen dan penyimpanan
    AGENTS_FILE: str = "/config/agents.conf"
    RUNNERS_FILE: str = "/config/runners.conf"
    RUNNER_DOWNLOAD_MAX_CONCURRENT: int = 2   # per proses web; terpisah dari batas upload
    RUNNER_DOWNLOAD_MAX_MBPS: int = 500       # per unduhan
    RUNNER_MAX_TOTAL: int = 2                 # tugas berjalan bersamaan di semua runner
    DEFAULT_RUNNER: str = ""                  # runner uji dan cadangan untuk container baru; kosong = diatur manual
    RESTORE_TEST_DEFAULT: str = "30 3 * * 0"   # jadwal uji bawaan: tiap Minggu 03:30 (jam DISPLAY_TIMEZONE)
    STANDBY_FULL_DEFAULT: str = "0 3 * * *"      # pemulihan full database cadangan: tiap hari 03:00
    STANDBY_BINLOG_DEFAULT: str = "0 * * * *"    # penerapan binlog baru: tiap jam
    STANDBY_LAG_HOURS: int = Field(default=3, ge=1)   # binlog yang sudah ada tapi belum diterapkan lebih lama dari ini = alert
    JOB_TIMEOUT_MINUTES: int = 60             # diperpanjang tiap laporan langkah
    DATA_DIR: str = "/data"              # mount NFS
    MOUNT_MARKER: str = ".lumbung-mounted"   # kosong = cek penanda dimatikan
    MOUNT_PROBE_TIMEOUT_SECONDS: int = 10
    NAS_SCAN_STALL_SECONDS: int = 300        # pindai ulang berhenti bila satu file tak selesai dibaca selama ini
    UPLOAD_MAX_CONCURRENT: int = 4       # total semua proses web; dibagi rata per proses
    WEB_CONCURRENCY: int = 2
    NAS_STATUS_STALE_SECONDS: int = 300  # status lebih tua dari ini = pengecek berhenti; upload ditolak
    # Kunci publik age, dipisah koma. Kunci privat tidak pernah ada di server ini.
    BACKUP_AGE_RECIPIENTS: str = Field(min_length=62)
    # IMAGE_TAG di .env yang disediakan Tambah container; bagian -aN sekaligus versi agen terbaru (app/core/versions.py)
    IMAGE_TAG_DEFAULT: str = "12.3-a2"
    RUNNER_VERSION_LATEST: str = "r2"    # tag image runner terbaru
    INSTALL_COMMAND_HOURS: int = 2       # batas waktu perintah pasang di Tambah container
    MANUAL_RESTORE_MAX_HOURS: int = 24   # batas terpanjang satu sesi Restore manual, termasuk perpanjangan

    # Pengecek alert (proses worker)
    CHECK_INTERVAL_SECONDS: int = 60
    TELEGRAM_API_BASE: str = "https://api.telegram.org"
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    NOTIFY_MAX_AGE_HOURS: int = Field(default=24, ge=1)     # pesan yang tak juga terkirim sesudah ini dibuang
    NOTIFY_STUCK_MINUTES: int = Field(default=30, ge=1)     # tertahan selama ini = alert di dashboard
    ALERT_DISK_FREE_PERCENT: int = 10
    ALERT_HEARTBEAT_MINUTES: int = 10
    ALERT_FULL_HOURS: int = 36
    ALERT_DUMP_HOURS: int = 36
    ALERT_USERS_HOURS: int = 36
    ALERT_DB_EXPECTED_DAYS: int = 30
    RENOTIFY_HOURS: int = 12
    BACKUP_REQUEST_TTL_MINUTES: int = 15

    # Retensi di NAS: harian 30 hari, bulanan 12 bulan, binlog 3 hari. Menghapus file; lihat app/retention/rules.py.
    RETENTION_ENABLED: bool = True
    RETENTION_DAILY_DAYS: int = Field(default=30, ge=1)
    RETENTION_MONTHS: int = Field(default=12, ge=0)
    RETENTION_BINLOG_DAYS: int = Field(default=3, ge=1)
    RETENTION_BATCH: int = Field(default=200, ge=1)     # file per putaran, supaya satu putaran tetap singkat
    RETENTION_TIMEOUT_SECONDS: int = 120

    # Kesehatan database dan laporan (tahap 3). Angka awal, belum diukur pada database sebenarnya.
    METRICS_RETENTION_DAYS: int = 90
    REPORT_HOUR: int = Field(default=7, ge=0, le=23)   # jam lokal (DISPLAY_TIMEZONE) laporan harian dan mingguan terbit
    REPORT_CONN_WARN_PERCENT: int = 80
    REPORT_HITRATIO_MIN: int = 95
    REPORT_SLOW_QUERIES_MAX: int = 100
    REPORT_DISK_WARN_DAYS: int = 14
    REPORT_DISK_BAD_DAYS: int = 7
    REPORT_FORECAST_DAYS: int = 14              # riwayat disk yang dipakai regresi

    # Lapisan AI (tahap 4). Mati bila AI_PROVIDER kosong atau "none". Hanya metrik teragregasi dan metadata yang dikirim;
    # query dinormalisasi lebih dulu (app/ai/normalize.py). Penyedia: claude | ollama | fake
    AI_PROVIDER: str = "none"
    AI_MODEL: str = "claude-haiku-5-5"
    AI_API_KEY: str = ""
    AI_BASE_URL: str = "https://api.anthropic.com"     # Ollama: mis. http://10.0.0.5:11434
    AI_TIMEOUT_SECONDS: int = Field(default=60, ge=5)
    AI_MAX_TOKENS: int = Field(default=800, ge=100)

    @field_validator("BACKUP_AGE_RECIPIENTS")
    @classmethod
    def _valid_recipients(cls, value: str) -> str:
        if not all(re.fullmatch(r"age1[0-9a-z]{58}", item) for item in value.split(",")):
            raise ValueError("BACKUP_AGE_RECIPIENTS harus berisi kunci publik age, dipisah koma tanpa spasi")
        return value


settings = Settings()
