"""Rekap harian per container untuk laporan bulanan, 3 bulanan, 6 bulanan, dan tahunan.

Hari dihitung menurut DISPLAY_TIMEZONE. Worker memanggil `rollup` tiap jam untuk kemarin dan hari ini; cuplikan per menit
disimpan 50 jam, jadi kemarin selalu dihitung ulang dari data utuh dan baris hari ini terus bertambah sampai lengkap.

Dari cuplikan per menit (status_samples), tiap pasangan cuplikan berurutan masuk ke hari milik cuplikan kedua:
- Total (kueri, Com_*, sesi baru, byte, slow query, buffer pool): jumlah selisih penghitung. Bila MariaDB restart di
  antaranya (Uptime mundur, penghitung turun, atau Uptime bertambah jauh lebih sedikit dari jam dinding), penghitung mulai
  dari nol, jadi nilai cuplikan kedua seluruhnya dihitung.
- Rata-rata laju dan CPU: total dibagi detik yang tercakup. Puncak hanya dari pasangan tanpa celah heartbeat (GAP).
- Koneksi dan memori: rata-rata dan puncak nilai cuplikan.
Dari metrik per jam (metrics): deadlock dan koneksi ditolak (jumlah), ukuran seluruh database (jam terakhir yang punya
ukuran), dan ruang disk backup (terendah)."""
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.reports.ingest import SIZE_DB
from app.reports.models import DailyMetric, Metric, StatusSample

TOTALS = {
    "queries": "Questions", "select": "Com_select", "insert": "Com_insert", "update": "Com_update",
    "delete": "Com_delete", "sessions": "Connections", "bytes_in": "Bytes_received", "bytes_out": "Bytes_sent",
    "slow_queries": "Slow_queries", "bp_requests": "Innodb_buffer_pool_read_requests",
    "bp_reads": "Innodb_buffer_pool_reads",
}
CPU = "cpu_seconds"
GAP = timedelta(minutes=3)   # sama dengan grafik aktivitas: celah heartbeat lebih lama tidak dipakai untuk puncak
CLOCK_SLACK = 120      # detik; Uptime yang tertinggal lebih dari ini dari jam dinding berarti restart di tengah celah
HOURLY_SUMS = {"deadlocks": "deadlocks", "conn_refused": "conn_refused"}


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Awal dan akhir hari lokal dalam UTC naive, seperti kolom `at`."""
    tz = ZoneInfo(settings.DISPLAY_TIMEZONE)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(UTC).replace(tzinfo=None), end.astimezone(UTC).replace(tzinfo=None)


def local_day(now: datetime) -> date:
    return now.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE)).date()


class _Peak:
    def __init__(self) -> None:
        self.value: float | None = None
        self.at: datetime | None = None

    def add(self, value: float, at: datetime) -> None:
        if self.value is None or value > self.value:
            self.value, self.at = value, at


def summarize(samples: list[StatusSample], start: datetime) -> dict[str, tuple[float, datetime | None]]:
    """Rekap satu hari dari cuplikan berurut waktu. Cuplikan sebelum `start` (paling banyak satu) hanya menjadi acuan."""
    out: dict[str, tuple[float, datetime | None]] = {}
    in_day = [s for s in samples if s.at >= start]
    if not in_day:
        return out
    out["minutes"] = (len(in_day), None)

    totals: dict[str, float] = defaultdict(float)
    seconds = cpu_seconds = cpu_capacity = 0.0
    restarts = 0
    qps, cpu = _Peak(), _Peak()
    for before, after in zip(samples, samples[1:], strict=False):
        if after.at < start:
            continue
        wall = (after.at - before.at).total_seconds()
        elapsed = after.uptime - before.uptime
        restarted = elapsed <= 0 or elapsed < wall - CLOCK_SLACK or any(
            after.data[n] < before.data[n] for n in (*TOTALS.values(), CPU) if n in after.data and n in before.data)
        if restarted:
            restarts += 1
            elapsed = min(after.uptime, wall) if wall > 0 else after.uptime
        deltas = {}
        for key, name in TOTALS.items():
            if name in after.data and (restarted or name in before.data):
                deltas[key] = after.data[name] - (0 if restarted else before.data[name])
                totals[key] += deltas[key]
        if elapsed <= 0:
            continue
        seconds += elapsed
        cpus = after.data.get("cpus") or 1
        if CPU in after.data and (restarted or CPU in before.data):
            used = after.data[CPU] - (0 if restarted else before.data[CPU])
            cpu_seconds += used
            cpu_capacity += elapsed * cpus
            if not restarted and after.at - before.at <= GAP:
                cpu.add(used / elapsed / cpus * 100, after.at)
        if not restarted and after.at - before.at <= GAP and "queries" in deltas:
            qps.add(deltas["queries"] / elapsed, after.at)

    for key, value in totals.items():
        out[key] = (value, None)
    out["restarts"] = (restarts, None)
    if seconds and "queries" in totals:
        out["qps_avg"] = (totals["queries"] / seconds, None)
    if qps.value is not None:
        out["qps_peak"] = (qps.value, qps.at)
    if cpu_capacity:
        out["cpu_avg"] = (cpu_seconds / cpu_capacity * 100, None)
    if cpu.value is not None:
        out["cpu_peak"] = (cpu.value, cpu.at)

    for key, name in (("threads", "Threads_connected"), ("running", "Threads_running"), ("rss", "rss_bytes")):
        values = [(s.data[name], s.at) for s in in_day if name in s.data]
        if values:
            peak = max(values, key=lambda v: v[0])
            out[f"{key}_avg"] = (sum(v for v, _ in values) / len(values), None)
            out[f"{key}_max"] = (peak[0], peak[1])
    for key, name in (("max_connections", "max_connections"), ("memory_limit", "memory_limit"), ("cpus", "cpus")):
        last = next((s.data[name] for s in reversed(in_day) if s.data.get(name) is not None), None)
        if last is not None:
            out[key] = (last, None)
    return out


def summarize_hourly(rows: list[Metric]) -> dict[str, tuple[float, datetime | None]]:
    out: dict[str, tuple[float, datetime | None]] = {}
    for key, name in HOURLY_SUMS.items():
        values = [r.value for r in rows if r.name == name]
        if values:
            out[key] = (sum(values), None)
    sizes = [r for r in rows if r.name.startswith(SIZE_DB)]
    if sizes:
        last = max(r.at for r in sizes)
        out["size_total"] = (sum(r.value for r in sizes if r.at == last), last)
    free = [r for r in rows if r.name == "backup_disk_free"]
    if free:
        low = min(free, key=lambda r: r.value)
        out["disk_free_min"] = (low.value, low.at)
    return out


async def rollup_day(db: AsyncSession, day: date) -> int:
    """Hitung ulang rekap satu hari untuk semua container yang punya data hari itu. Mengembalikan jumlah container."""
    start, end = day_bounds(day)
    samples = (await db.execute(
        select(StatusSample).where(StatusSample.at >= start - GAP, StatusSample.at < end)
        .order_by(StatusSample.container, StatusSample.at))).scalars().all()
    hourly = (await db.execute(
        select(Metric).where(Metric.at >= start, Metric.at < end,
                             (Metric.name.in_([*HOURLY_SUMS.values(), "backup_disk_free"]))
                             | Metric.name.startswith(SIZE_DB)))).scalars().all()
    by_container: dict[str, list[StatusSample]] = defaultdict(list)
    for s in samples:
        by_container[s.container].append(s)
    # Acuan sebelum tengah malam yang lebih tua dari GAP: ambil cuplikan terakhir sebelum awal hari.
    for name, rows in by_container.items():
        if rows[0].at >= start:
            previous = await db.scalar(select(StatusSample).where(
                StatusSample.container == name, StatusSample.at < start).order_by(StatusSample.at.desc()).limit(1))
            if previous is not None:
                rows.insert(0, previous)
    hourly_by: dict[str, list[Metric]] = defaultdict(list)
    for r in hourly:
        hourly_by[r.container].append(r)

    written = 0
    for name in sorted(set(by_container) | set(hourly_by)):
        values = {**summarize(by_container.get(name, []), start), **summarize_hourly(hourly_by.get(name, []))}
        await db.execute(delete(DailyMetric).where(DailyMetric.container == name, DailyMetric.day == day))
        if values:
            db.add_all(DailyMetric(container=name, day=day, name=key, value=float(value), peak_at=at)
                       for key, (value, at) in values.items())
            written += 1
    return written


async def rollup(db: AsyncSession) -> None:
    """Kemarin dan hari ini (lokal), satu transaksi per hari."""
    now = await db.scalar(select(func.now()))
    await db.rollback()
    today = local_day(now)
    for day in (today - timedelta(days=1), today):
        async with db.begin():
            await rollup_day(db, day)
