from datetime import datetime

from sqlalchemy import JSON, CHAR, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

NAME_LENGTH = 64


class Container(Base):
    """Waktu terdaftar dan asal container. Tanpa token, dan bukan sumber status aktif: itu hanya agents.conf."""

    __tablename__ = "containers"

    name: Mapped[str] = mapped_column(String(NAME_LENGTH), primary_key=True)
    registered_at: Mapped[datetime] = mapped_column(server_default=func.now())
    origin: Mapped[str] = mapped_column(String(20), default="file")   # dashboard | file (tahap 2: promote | manual)
    source_container: Mapped[str | None] = mapped_column(String(NAME_LENGTH))


class Heartbeat(Base):
    """Heartbeat terakhir tiap container. Daftar server disusun dari sini."""

    __tablename__ = "heartbeats"

    container: Mapped[str] = mapped_column(String(NAME_LENGTH), primary_key=True)
    server: Mapped[str | None] = mapped_column(String(255))
    machine_id: Mapped[str | None] = mapped_column(CHAR(64))
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    info: Mapped[dict] = mapped_column(JSON)
