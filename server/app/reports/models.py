from datetime import datetime

from sqlalchemy import JSON, BigInteger, Double, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base

NAME = 191


class Metric(Base):
    """Deret waktu per jam. Gauge menyimpan nilai terakhir beserta puncak dan waktu puncaknya; penghitung menyimpan
    jumlah selisih dalam jam itu. Tidak ada isi data, hanya angka."""

    __tablename__ = "metrics"
    __table_args__ = (Index("ix_metrics_name_at", "name", "at"),)

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"), primary_key=True)
    at: Mapped[datetime] = mapped_column(primary_key=True)        # awal jam, UTC
    name: Mapped[str] = mapped_column(String(NAME), primary_key=True)
    value: Mapped[float] = mapped_column(Double)
    peak: Mapped[float | None] = mapped_column(Double)
    peak_at: Mapped[datetime | None] = mapped_column()


class MetricCounter(Base):
    """Nilai kumulatif terakhir tiap penghitung SHOW GLOBAL STATUS, untuk menghitung selisih dan mendeteksi restart."""

    __tablename__ = "metric_counters"

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"), primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME), primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger)
    uptime: Mapped[int] = mapped_column(BigInteger)


class Report(Base):
    """Snapshot saat terbit; tidak diubah sesudahnya."""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("kind", "period_start", name="uq_reports_period"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))                 # daily | weekly
    period_start: Mapped[datetime] = mapped_column()
    period_end: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    data: Mapped[dict] = mapped_column(JSON)
    sent_at: Mapped[datetime | None] = mapped_column()
