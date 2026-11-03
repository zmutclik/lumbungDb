from datetime import datetime

from sqlalchemy import CHAR, JSON, BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.backups.models import Backup
from app.containers.models import NAME_LENGTH
from app.core.database import Base

CRON_LENGTH = 120


class RunnerHeartbeat(Base):
    """Heartbeat terakhir tiap runner. Daftar runner dan status aktifnya hanya ada di runners.conf."""

    __tablename__ = "runner_heartbeats"

    runner: Mapped[str] = mapped_column(String(NAME_LENGTH), primary_key=True)
    server: Mapped[str | None] = mapped_column(String(255))
    machine_id: Mapped[str | None] = mapped_column(CHAR(64))
    version: Mapped[str | None] = mapped_column(String(40))
    scratch_total: Mapped[int | None] = mapped_column(BigInteger)
    scratch_free: Mapped[int | None] = mapped_column(BigInteger)
    at: Mapped[datetime] = mapped_column(server_default=func.now())


class ContainerSettings(Base):
    """Penetapan runner dan jadwal uji per container."""

    __tablename__ = "container_settings"

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"), primary_key=True)
    test_runner: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    standby_runner: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    test_schedule: Mapped[str | None] = mapped_column(String(CRON_LENGTH))   # kosong = uji terjadwal mati
    test_full: Mapped[bool] = mapped_column(default=True)
    test_databases: Mapped[list | None] = mapped_column(JSON)                # kosong = semua database


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_runner", "runner", "status", "id"),
        Index("ix_jobs_container", "container", "id"),
        Index("ix_jobs_deadline", "status", "deadline_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    runner: Mapped[str] = mapped_column(String(NAME_LENGTH))
    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    kind: Mapped[str] = mapped_column(String(20))           # restore_test
    status: Mapped[str] = mapped_column(String(20))         # lihat service.STATUS_*
    step: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(500))
    requested_by: Mapped[str | None] = mapped_column(String(100))    # kosong = terjadwal
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column()
    deadline_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class JobFile(Base):
    """Satu-satunya dasar izin unduh: runner hanya bisa mengambil file yang tercatat di tugasnya."""

    __tablename__ = "job_files"

    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id"), primary_key=True)
    backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Backup.id), primary_key=True)


class JobDownload(Base):
    __tablename__ = "job_downloads"
    __table_args__ = (Index("ix_job_downloads_job", "job_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id"))
    backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(Backup.id))
    runner: Mapped[str] = mapped_column(String(NAME_LENGTH))
    offset: Mapped[int] = mapped_column(BigInteger)
    ip: Mapped[str] = mapped_column(String(45))
    at: Mapped[datetime] = mapped_column(server_default=func.now())


class RestoreTest(Base):
    """Hasil uji per jenis. Bukti untuk retensi: hanya baris Lolos yang melindungi file."""

    __tablename__ = "restore_tests"
    __table_args__ = (
        Index("ix_restore_tests_container", "container", "kind", "status", "id"),
        Index("ix_restore_tests_backup", "backup_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id"))
    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"))
    runner: Mapped[str] = mapped_column(String(NAME_LENGTH))
    kind: Mapped[str] = mapped_column(String(12))                    # full | dump | users | binlog
    database: Mapped[str | None] = mapped_column(String(64))
    backup_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(Backup.id))
    status: Mapped[str] = mapped_column(String(20))                  # Lolos | Gagal | Tidak terlaksana
    failed_step: Mapped[str | None] = mapped_column(String(200))
    duration_seconds: Mapped[int | None] = mapped_column()
    log: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
