from datetime import datetime

from sqlalchemy import BigInteger, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Notification(Base):
    """Antrean kirim dan riwayat notifikasi Telegram. Pesan tidak hilang bila Telegram sedang tidak terjangkau."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_due", "status", "next_attempt_at"), Index("ix_notifications_created", "created_at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))                # alert | laporan
    ref: Mapped[str | None] = mapped_column(String(64))          # mis. report:12
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="Menunggu")   # Menunggu | Terkirim | Kedaluwarsa | Hanya log
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column()
