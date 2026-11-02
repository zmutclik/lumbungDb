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
