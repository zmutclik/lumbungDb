from pydantic import Field
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
    DATA_DIR: str = "/data"              # mount NFS
    MOUNT_MARKER: str = ".lumbung-mounted"   # kosong = cek penanda dimatikan
    MOUNT_PROBE_TIMEOUT_SECONDS: int = 10
    UPLOAD_MAX_CONCURRENT: int = 4       # total semua proses web; dibagi rata per proses
    WEB_CONCURRENCY: int = 2
    NAS_STATUS_STALE_SECONDS: int = 300  # status lebih tua dari ini = pengecek berhenti; upload ditolak
    IMAGE_TAG_DEFAULT: str = "12.3-a1"   # nilai contoh di .env yang disediakan Tambah container
    INSTALL_COMMAND_HOURS: int = 2       # batas waktu perintah pasang di Tambah container

    # Pengecek alert (proses worker)
    CHECK_INTERVAL_SECONDS: int = 60
    TELEGRAM_API_BASE: str = "https://api.telegram.org"
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    ALERT_DISK_FREE_PERCENT: int = 10
    ALERT_HEARTBEAT_MINUTES: int = 10
    ALERT_FULL_HOURS: int = 36
    ALERT_DUMP_HOURS: int = 36
    ALERT_USERS_HOURS: int = 36
    ALERT_DB_EXPECTED_DAYS: int = 30
    RENOTIFY_HOURS: int = 12
    BACKUP_REQUEST_TTL_MINUTES: int = 15


settings = Settings()
