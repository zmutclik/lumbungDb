from datetime import datetime

from sqlalchemy import CHAR, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.containers.models import NAME_LENGTH
from app.core.database import Base


class RunnerInstall(Base):
    """Perintah pasang terakhir tiap runner. Hanya hash token; perintah baru menggantikan yang lama.

    Tanpa foreign key: daftar runner hanya ada di runners.conf.
    """

    __tablename__ = "runner_installs"

    runner: Mapped[str] = mapped_column(String(NAME_LENGTH), primary_key=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime]
    machine_id: Mapped[str | None] = mapped_column(CHAR(64))    # terisi saat script pertama kali berjalan
    claimed_at: Mapped[datetime | None]
