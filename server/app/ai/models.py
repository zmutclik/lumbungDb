from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiSummary(Base):
    """Ringkasan AI satu laporan, disimpan bersama persis masukan yang dikirim ke model (untuk audit). Tidak mengubah
    laporan, alert, atau notifikasi: laporan angka terbit sendiri tanpa baris ini."""

    __tablename__ = "ai_summaries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("reports.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    input: Mapped[dict] = mapped_column(JSON)
    text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AiExplanation(Base):
    """Penjelasan AI atas keadaan satu container, diminta admin. Tersimpan bersama masukan persisnya (untuk audit).
    Tidak mengubah atau menekan alert."""

    __tablename__ = "ai_explanations"
    __table_args__ = (Index("ix_ai_explanations_container", "container", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    container: Mapped[str] = mapped_column(String(64), ForeignKey("containers.name"))
    requested_by: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    input: Mapped[dict] = mapped_column(JSON)
    text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
