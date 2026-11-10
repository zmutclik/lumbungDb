from datetime import datetime

from sqlalchemy import CHAR, BigInteger, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.backups.models import Backup
from app.containers.models import NAME_LENGTH
from app.core.database import Base


class ManualRestore(Base):
    """Satu sesi Restore manual. Hanya hash token yang disimpan; token tampil sekali saat sesi dibuat."""

    __tablename__ = "manual_restores"
    __table_args__ = (Index("ix_manual_restores_source", "source", "id"), Index("ix_manual_restores_status", "status", "expires_at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    new_name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    port: Mapped[int] = mapped_column()
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    full_backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Backup.id))
    binlog: Mapped[str] = mapped_column(String(10))               # all | none
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    max_expires_at: Mapped[datetime]
    machine_id: Mapped[str | None] = mapped_column(CHAR(64))      # terikat saat script pertama berjalan
    claimed_at: Mapped[datetime | None]
    step: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="Aktif")   # Aktif | Berjalan | Gagal | Selesai | Dicabut
    note: Mapped[str | None] = mapped_column(String(500))
    finished_at: Mapped[datetime | None]


class ManualRestoreFile(Base):
    """Satu-satunya dasar izin unduh sesi ini, sekaligus perlindungan retensi selama sesi aktif."""

    __tablename__ = "manual_restore_files"

    restore_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("manual_restores.id"), primary_key=True)
    backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Backup.id), primary_key=True)


class ManualRestoreDownload(Base):
    __tablename__ = "manual_restore_downloads"
    __table_args__ = (Index("ix_manual_restore_downloads_restore", "restore_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    restore_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("manual_restores.id"))
    backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Backup.id))
    offset: Mapped[int] = mapped_column(BigInteger)
    ip: Mapped[str] = mapped_column(String(45))
    at: Mapped[datetime] = mapped_column(server_default=func.now())
