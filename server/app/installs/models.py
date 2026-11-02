from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base


class ContainerInstall(Base):
    """Perintah pasang terakhir tiap container. Hanya hash token; perintah baru menggantikan yang lama."""

    __tablename__ = "container_installs"

    container: Mapped[str] = mapped_column(
        String(NAME_LENGTH), ForeignKey("containers.name", ondelete="CASCADE"), primary_key=True,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    port: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime]
    machine_id: Mapped[str | None] = mapped_column(CHAR(64))    # terisi saat script pertama kali berjalan
    claimed_at: Mapped[datetime | None]
