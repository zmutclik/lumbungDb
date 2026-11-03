from datetime import datetime

from sqlalchemy import BigInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NasStatus(Base):
    """Hasil pemeriksaan NAS terakhir. Satu baris. Ada di database, bukan memori: ditulis proses worker dan dibaca
    semua proses web. Upload, dashboard, dan health membaca baris ini, tidak pernah menyentuh NAS."""

    __tablename__ = "nas_status"

    id: Mapped[int] = mapped_column(primary_key=True, default=1, autoincrement=False)
    state: Mapped[str] = mapped_column(String(10))   # ok | low | down | hang
    since: Mapped[datetime] = mapped_column()
    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checked_at: Mapped[datetime] = mapped_column(server_default=func.now())


class NasScan(Base):
    """Pindai ulang NAS: membangun lagi metadata backup dari file yang ada, bila database metadata hilang."""

    __tablename__ = "nas_scans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(10), default="Menunggu")     # Menunggu | Berjalan | Selesai | Gagal
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    seen: Mapped[int] = mapped_column(default=0)
    added: Mapped[int] = mapped_column(default=0)
    skipped: Mapped[int] = mapped_column(default=0)        # nama tidak dikenal atau container tidak terdaftar
    note: Mapped[str | None] = mapped_column(String(500))
