from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base


class Alert(Base):
    """Alert yang sedang aktif, untuk mencegah notifikasi berulang."""

    __tablename__ = "alerts"

    key: Mapped[str] = mapped_column(String(191), primary_key=True)
    container: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    cond: Mapped[str] = mapped_column(String(500))
    since: Mapped[datetime] = mapped_column(server_default=func.now())
    last_sent: Mapped[datetime | None] = mapped_column()
