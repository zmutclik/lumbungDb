from datetime import datetime

from sqlalchemy import CHAR, BigInteger, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base


class Backup(Base):
    """Satu baris per file atau versi yang diterima. Isi file hanya ada di NAS."""

    __tablename__ = "backups"
    __table_args__ = (
        UniqueConstraint("container", "kind", "relpath", "plain_sha256", name="uq_backups_version"),
        Index("ix_backups_latest", "container", "kind", "database", "backup_at"),   # backup terakhir, linimasa
        Index("ix_backups_binlog", "container", "kind", "binlog_no"),               # rantai binlog
        Index("ix_backups_page", "container", "id"),                                # riwayat, keyset
        Index("ix_backups_at", "backup_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    kind: Mapped[str] = mapped_column(String(12))                  # fullbackup | databases | binlog
    database: Mapped[str | None] = mapped_column(String(64))
    relpath: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(512))          # relatif terhadap folder NAS
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64))                  # file terenkripsi di NAS
    plain_sha256: Mapped[str] = mapped_column(CHAR(64))            # isi sebelum enkripsi, dari agen: pengenal versi
    backup_at: Mapped[datetime] = mapped_column()                  # waktu pembuatan: dasar umur dan alert
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    request_id: Mapped[int | None] = mapped_column(BigInteger)     # kosong = backup terjadwal
    binlog_no: Mapped[int | None] = mapped_column()
    chain_id: Mapped[int | None] = mapped_column()
    binlog_start: Mapped[str | None] = mapped_column(String(64))


class IntegrityEvent(Base):
    """Nama file sama dengan isi berbeda. Kedua versi tersimpan di NAS; ini antrean tinjauannya."""

    __tablename__ = "integrity_events"
    __table_args__ = (Index("ix_integrity_open", "container", "reviewed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    kind: Mapped[str] = mapped_column(String(12))
    relpath: Mapped[str] = mapped_column(String(255))
    first_backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("backups.id"))
    new_backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("backups.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column()
