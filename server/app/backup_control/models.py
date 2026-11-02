from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base

CRON_LENGTH = 120


class BackupSchedule(Base):
    """Jadwal yang diminta LumbungDB beserta versinya, dan jadwal yang berlaku menurut laporan agen."""

    __tablename__ = "backup_schedules"

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"), primary_key=True)
    requested_full: Mapped[str] = mapped_column(String(CRON_LENGTH))
    requested_dump: Mapped[str] = mapped_column(String(CRON_LENGTH))
    requested_binlog: Mapped[str] = mapped_column(String(CRON_LENGTH))
    requested_version: Mapped[int] = mapped_column()
    applied_version: Mapped[int] = mapped_column()
    effective_full: Mapped[str] = mapped_column(String(CRON_LENGTH))
    effective_dump: Mapped[str] = mapped_column(String(CRON_LENGTH))
    effective_binlog: Mapped[str] = mapped_column(String(CRON_LENGTH))
    source: Mapped[str] = mapped_column(String(10))     # bawaan | env | lumbung
    error_version: Mapped[int | None] = mapped_column()
    error_message: Mapped[str | None] = mapped_column(String(300))
    reported_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BackupRequest(Base):
    __tablename__ = "backup_requests"
    __table_args__ = (
        Index("ix_requests_container", "container", "id"),
        Index("ix_requests_status", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    kind: Mapped[str] = mapped_column(String(10))       # full | dump | binlog
    database: Mapped[str | None] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="Menunggu")
    note: Mapped[str | None] = mapped_column(String(500))
    log: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class ControlLog(Base):
    """Riwayat perubahan jadwal dan permintaan backup: siapa, kapan, nilai lama dan baru."""

    __tablename__ = "control_log"
    __table_args__ = (Index("ix_control_container", "container", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(1000))
