"""Log slow query per container, disusun dari penghitung performance_schema yang dikirim agen tiap jam.

Agen mengirim pola query yang pernah berjalan >= 1 detik beserta penghitung kumulatifnya. Di sini dihitung selisih sejak
kiriman sebelumnya; selisih yang rata-ratanya >= SLOW_QUERY_SECONDS dicatat pada jam itu. Kiriman pertama sebuah container
hanya menjadi acuan, karena penghitungnya berisi seluruh riwayat sejak MariaDB mulai yang tidak bisa diletakkan di satu jam.
Teks pola dinormalisasi ulang (data agen tidak dipercaya), jadi tidak ada nilai literal yang tersimpan.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.normalize import normalize_query
from app.backup_control.models import ControlLog
from app.core.config import settings
from app.slow_queries.models import COUNTERS, DIGEST_LENGTH, TEXT_LENGTH, SlowQueryCursor, SlowQueryLog

PS_PER_SECOND = 10**12
MAX_PATTERNS = 200
CLEAR_ACTION = "Kosongkan log slow query"


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _parse(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    database, digest, text = item.get("database"), item.get("digest"), item.get("text")
    if not isinstance(database, str) or not isinstance(digest, str) or not digest or not isinstance(text, str):
        return None
    values = {name: _int(item.get(name, 0)) for name in COUNTERS}
    max_ps = _int(item.get("max_ps", 0))
    if None in values.values() or max_ps is None or values["count"] is None:
        return None
    return {"database": database[:64], "digest": digest[:DIGEST_LENGTH], "text": normalize_query(text)[:TEXT_LENGTH],
            "max_ps": max_ps, **values}


async def ingest(db: AsyncSession, container: str, items: list, uptime: int | None, at: datetime) -> None:
    """Dipanggil di dalam transaksi heartbeat. `at` = awal jam (UTC)."""
    first = not await db.scalar(select(exists().where(SlowQueryCursor.container == container)))
    for raw in items:
        item = _parse(raw)
        if item is None:
            continue
        values = {name: item[name] for name in COUNTERS}
        cursor = await db.get(SlowQueryCursor, (container, item["database"], item["digest"]), with_for_update=True)
        if cursor is None:
            db.add(SlowQueryCursor(container=container, database=item["database"], digest=item["digest"],
                                   uptime=uptime or 0, **values))
            if first:
                continue
            delta = values   # pola baru sejak kiriman sebelumnya
        else:
            restarted = (uptime is not None and uptime < cursor.uptime) or values["count"] < cursor.count
            delta = values if restarted else {name: max(values[name] - getattr(cursor, name), 0) for name in COUNTERS}
            for name in COUNTERS:
                setattr(cursor, name, values[name])
            cursor.uptime = uptime or 0
        if not delta["count"] or delta["total_ps"] / delta["count"] < settings.SLOW_QUERY_SECONDS * PS_PER_SECOND:
            continue
        stmt = insert(SlowQueryLog).values(container=container, at=at, database=item["database"],
                                           digest=item["digest"], text=item["text"], max_ps=item["max_ps"], **delta)
        await db.execute(stmt.on_duplicate_key_update({
            "text": stmt.inserted.text, "max_ps": stmt.inserted.max_ps,
            **{name: getattr(SlowQueryLog, name) + getattr(stmt.inserted, name) for name in COUNTERS},
        }))


@dataclass
class Pattern:
    database: str
    digest: str
    text: str
    count: int
    total_ps: int
    max_ps: int
    rows_examined: int
    rows_sent: int
    no_index_used: int
    no_good_index_used: int
    full_join: int
    tmp_disk_tables: int
    first_at: datetime
    last_at: datetime
    hours: int

    @property
    def avg_seconds(self) -> float:
        return self.total_ps / self.count / PS_PER_SECOND if self.count else 0.0

    @property
    def total_seconds(self) -> float:
        return self.total_ps / PS_PER_SECOND

    @property
    def max_seconds(self) -> float:
        return self.max_ps / PS_PER_SECOND

    @property
    def rows_per_exec(self) -> float:
        return self.rows_examined / self.count if self.count else 0.0

    def as_dict(self) -> dict:
        return {"database": self.database, "text": self.text, "count": self.count,
                "total_seconds": round(self.total_seconds, 3), "avg_seconds": round(self.avg_seconds, 3),
                "max_seconds": round(self.max_seconds, 3), "rows_per_exec": round(self.rows_per_exec),
                "no_index_used": self.no_index_used, "hours": self.hours}


async def patterns(db: AsyncSession, container: str, start: datetime, end: datetime,
                   limit: int = MAX_PATTERNS) -> list[Pattern]:
    """Pola slow query dalam rentang [start, end), terberat (total waktu) lebih dulu."""
    log = SlowQueryLog
    rows = await db.execute(
        select(log.database, log.digest, func.max(log.text).label("text"),
               *[func.sum(getattr(log, name)).label(name) for name in COUNTERS],
               func.max(log.max_ps).label("max_ps"), func.min(log.at).label("first_at"),
               func.max(log.at).label("last_at"), func.count().label("hours"))
        .where(log.container == container, log.at >= start, log.at < end)
        .group_by(log.database, log.digest)
        .order_by(func.sum(log.total_ps).desc())
        .limit(limit)
    )
    return [Pattern(database=r.database, digest=r.digest, text=r.text, max_ps=int(r.max_ps), first_at=r.first_at,
                    last_at=r.last_at, hours=int(r.hours), **{name: int(getattr(r, name)) for name in COUNTERS})
            for r in rows]


async def oldest(db: AsyncSession, container: str) -> datetime | None:
    return await db.scalar(select(func.min(SlowQueryLog.at)).where(SlowQueryLog.container == container))


async def last_clear(db: AsyncSession, container: str) -> ControlLog | None:
    return await db.scalar(select(ControlLog).where(ControlLog.container == container, ControlLog.action == CLEAR_ACTION)
                           .order_by(ControlLog.id.desc()).limit(1))


async def clear(db: AsyncSession, container: str, actor: str) -> int:
    """Menghapus seluruh log slow query container ini. Acuan penghitung tetap, jadi yang lama tidak tercatat ulang."""
    async with db.begin():
        result = await db.execute(delete(SlowQueryLog).where(SlowQueryLog.container == container))
        db.add(ControlLog(container=container, actor=actor, action=CLEAR_ACTION,
                          detail=f"{result.rowcount} baris per jam dihapus"))
    return result.rowcount


async def purge(db: AsyncSession, before: datetime) -> None:
    """Dipanggil di dalam transaksi purge metrik."""
    await db.execute(delete(SlowQueryLog).where(SlowQueryLog.at < before))
