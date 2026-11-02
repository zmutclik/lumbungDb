from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.nas.models import NasStatus
from app.nas.monitor import ProbeResult


@dataclass(frozen=True)
class NasView:
    state: str                  # ok | low | down | hang | unknown (belum ada hasil, atau pengecek berhenti)
    since: datetime | None
    total: int | None
    free: int | None
    stale: bool

    @property
    def writable(self) -> bool:
        return self.state in ("ok", "low")

    @property
    def problem(self) -> bool:
        return self.state in ("down", "hang", "unknown")

    @property
    def free_percent(self) -> int | None:
        if not self.total:
            return None
        return round(self.free * 100 / self.total)


async def get_view(db: AsyncSession) -> NasView:
    row = (await db.execute(select(
        NasStatus.state, NasStatus.since, NasStatus.total_bytes, NasStatus.free_bytes,
        func.timestampdiff(literal_column("SECOND"), NasStatus.checked_at, func.now()).label("age"),
    ))).one_or_none()
    if row is None:
        return NasView("unknown", None, None, None, True)
    stale = row.age > settings.NAS_STATUS_STALE_SECONDS
    return NasView(
        "unknown" if stale else row.state, row.since, row.total_bytes, row.free_bytes, stale,
    )


async def save_result(db: AsyncSession, result: ProbeResult) -> None:
    """`since` hanya berubah saat keadaan berubah. Saat NAS tidak merespons, angka ruang terakhir dipertahankan."""
    async with db.begin():
        row = await db.get(NasStatus, 1, with_for_update=True)
        if row is None:
            db.add(NasStatus(id=1, state=result.state, since=func.now(), checked_at=func.now(),
                             total_bytes=result.total, free_bytes=result.free))
            return
        if row.state != result.state:
            row.state, row.since = result.state, func.now()
        row.checked_at = func.now()
        if result.total is not None:
            row.total_bytes, row.free_bytes = result.total, result.free
