from datetime import datetime

from sqlalchemy import BigInteger, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base


class RetentionLog(Base):
    """Catatan tiap file yang dihapus retensi: yang mana, kapan dibuat, dan kenapa."""

    __tablename__ = "retention_log"
    __table_args__ = (Index("ix_retention_log_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    container: Mapped[str] = mapped_column(String(NAME_LENGTH))
    kind: Mapped[str] = mapped_column(String(12))
    relpath: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    backup_at: Mapped[datetime] = mapped_column()
    reason: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(server_default=func.now())
