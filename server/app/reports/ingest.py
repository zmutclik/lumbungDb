"""Menyimpan metrik dari heartbeat. Dipanggil di dalam transaksi heartbeat."""
from datetime import datetime

from sqlalchemy import case, delete
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup_control.schemas import MetricsIn
from app.reports.models import NAME, Metric, MetricCounter

# Nama di SHOW GLOBAL STATUS → nama metrik
COUNTER_NAMES = {
    "Connection_errors_max_connections": "conn_refused",
    "Innodb_buffer_pool_read_requests": "bp_requests",
    "Innodb_buffer_pool_reads": "bp_reads",
    "Slow_queries": "slow_queries",
    "Innodb_deadlocks": "deadlocks",
}
SIZE_DB = "size_db:"
SIZE_TABLE = "size_table:"


def hour_start(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


async def _gauge(db: AsyncSession, container: str, at: datetime, name: str, value: float, now: datetime) -> None:
    stmt = insert(Metric).values(container=container, at=at, name=name[:NAME], value=value, peak=value, peak_at=now)
    # peak_at lebih dulu dari peak: MariaDB mengevaluasi penugasan dari kiri ke kanan.
    await db.execute(stmt.on_duplicate_key_update({
        "value": stmt.inserted.value,
        "peak_at": case((stmt.inserted.peak > Metric.peak, stmt.inserted.peak_at), else_=Metric.peak_at),
        "peak": case((stmt.inserted.peak > Metric.peak, stmt.inserted.peak), else_=Metric.peak),
    }))


async def _add(db: AsyncSession, container: str, at: datetime, name: str, delta: float) -> None:
    stmt = insert(Metric).values(container=container, at=at, name=name, value=delta)
    await db.execute(stmt.on_duplicate_key_update({"value": Metric.value + stmt.inserted.value}))


async def _counter_delta(db: AsyncSession, container: str, name: str, value: int, uptime: int) -> int:
    """Selisih sejak cuplikan sebelumnya. Cuplikan pertama hanya menjadi acuan. Setelah restart MariaDB (uptime
    mundur atau nilai turun) seluruh nilai baru dihitung, karena penghitung mulai dari nol."""
    row = await db.get(MetricCounter, (container, name), with_for_update=True)
    if row is None:
        db.add(MetricCounter(container=container, name=name, last_value=value, uptime=uptime))
        return 0
    restarted = uptime < row.uptime or value < row.last_value
    delta = value if restarted else value - row.last_value
    row.last_value, row.uptime = value, uptime
    return delta


async def ingest(db: AsyncSession, container: str, metrics: MetricsIn | None, disk: dict | None, now: datetime) -> None:
    at = hour_start(now)
    if disk and isinstance(disk.get("free"), int) and isinstance(disk.get("total"), int):
        await _gauge(db, container, at, "backup_disk_free", disk["free"], now)
        await _gauge(db, container, at, "backup_disk_total", disk["total"], now)
    if metrics is None:
        return
    status = metrics.status
    if "Threads_connected" in status:
        await _gauge(db, container, at, "connections", status["Threads_connected"], now)
    await _gauge(db, container, at, "max_connections", metrics.max_connections, now)
    uptime = status.get("Uptime", 0)
    for source, name in COUNTER_NAMES.items():
        if source in status:
            delta = await _counter_delta(db, container, name, status[source], uptime)
            if delta:
                await _add(db, container, at, name, delta)
    if metrics.sizes:
        for item in metrics.sizes.databases:
            if isinstance(item.get("name"), str) and isinstance(item.get("bytes"), int):
                await _gauge(db, container, at, SIZE_DB + item["name"], item["bytes"], now)
        for item in metrics.sizes.tables:
            if all(isinstance(item.get(k), t) for k, t in (("database", str), ("table", str), ("bytes", int))):
                await _gauge(db, container, at, f"{SIZE_TABLE}{item['database']}.{item['table']}", item["bytes"], now)


async def purge(db: AsyncSession, before: datetime) -> None:
    async with db.begin():
        await db.execute(delete(Metric).where(Metric.at < before))
