from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base

DIGEST_LENGTH = 64
TEXT_LENGTH = 300
# Penghitung kumulatif per pola di performance_schema yang dicatat selisihnya.
COUNTERS = ("count", "total_ps", "rows_examined", "rows_sent", "no_index_used", "no_good_index_used", "full_join",
            "tmp_disk_tables")


class _Counters:
    count: Mapped[int] = mapped_column(BigInteger)
    total_ps: Mapped[int] = mapped_column(BigInteger)            # pikodetik
    rows_examined: Mapped[int] = mapped_column(BigInteger, default=0)
    rows_sent: Mapped[int] = mapped_column(BigInteger, default=0)
    no_index_used: Mapped[int] = mapped_column(BigInteger, default=0)
    no_good_index_used: Mapped[int] = mapped_column(BigInteger, default=0)
    full_join: Mapped[int] = mapped_column(BigInteger, default=0)
    tmp_disk_tables: Mapped[int] = mapped_column(BigInteger, default=0)


class SlowQueryCursor(_Counters, Base):
    """Nilai kumulatif terakhir tiap pola, untuk menghitung selisih per jam dan mendeteksi restart MariaDB.
    Tidak ikut dihapus saat log dikosongkan: sesudahnya hanya eksekusi baru yang tercatat."""

    __tablename__ = "slow_query_cursors"

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"), primary_key=True)
    database: Mapped[str] = mapped_column(String(64), primary_key=True)
    digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), primary_key=True)
    uptime: Mapped[int] = mapped_column(BigInteger)


class SlowQueryLog(_Counters, Base):
    """Slow query per pola per jam: selisih penghitung performance_schema dalam jam itu. Teks pola tanpa nilai literal
    (dinormalisasi ulang di LumbungDB). Disimpan DAILY_METRICS_RETENTION_DAYS; admin bisa mengosongkannya per container."""

    __tablename__ = "slow_query_log"
    __table_args__ = (Index("ix_slow_query_log_at", "at"),)

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), ForeignKey("containers.name"), primary_key=True)
    at: Mapped[datetime] = mapped_column(primary_key=True)       # awal jam, UTC
    database: Mapped[str] = mapped_column(String(64), primary_key=True)
    digest: Mapped[str] = mapped_column(String(DIGEST_LENGTH), primary_key=True)
    text: Mapped[str] = mapped_column(String(TEXT_LENGTH))
    max_ps: Mapped[int] = mapped_column(BigInteger)              # eksekusi terlama sejak MariaDB mulai, saat dicatat
