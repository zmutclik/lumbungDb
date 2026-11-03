"""Menyusun dan menerbitkan laporan harian dan mingguan, serta perkiraan disk untuk alert."""
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry
from app.backups.models import Backup
from app.containers.models import Container, Heartbeat
from app.core.config import settings
from app.integrations import telegram
from app.reports import ingest, rules
from app.reports.models import Metric, Report

logger = logging.getLogger(__name__)
DISK_FREE = "backup_disk_free"


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def due_periods(now: datetime) -> list[tuple[str, datetime, datetime]]:
    """Periode yang sudah boleh terbit menurut jam lokal. Harian: kemarin. Mingguan: Senin sampai Minggu lalu."""
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE))
    if local.hour < settings.REPORT_HOUR:
        return []
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    periods = [("daily", _utc(today - timedelta(days=1)), _utc(today))]
    if local.weekday() == 0:
        periods.append(("weekly", _utc(today - timedelta(days=7)), _utc(today)))
    return periods


async def _sum(db: AsyncSession, container: str, name: str, start: datetime, end: datetime) -> float:
    value = await db.scalar(select(func.coalesce(func.sum(Metric.value), 0)).where(
        Metric.container == container, Metric.name == name, Metric.at >= ingest.hour_start(start), Metric.at < end))
    return float(value or 0)


async def disk_series(db: AsyncSession, now: datetime, containers: list[str] | None = None) -> dict[str, list]:
    since = now - timedelta(days=settings.REPORT_FORECAST_DAYS)
    stmt = select(Metric.container, Metric.at, Metric.value).where(Metric.name == DISK_FREE, Metric.at >= since)
    if containers is not None:
        stmt = stmt.where(Metric.container.in_(containers))
    series: dict[str, list] = {}
    for row in (await db.execute(stmt.order_by(Metric.container, Metric.at))).all():
        series.setdefault(row.container, []).append((row.at, row.value))
    return series


async def disk_forecasts(db: AsyncSession, now: datetime) -> dict[str, float]:
    """Container → sisa hari sampai /backup penuh, hanya yang ruangnya menyusut dan datanya cukup."""
    result = {}
    for container, points in (await disk_series(db, now)).items():
        days = rules.forecast_full_days(points)
        if days is not None:
            result[container] = days
    return result


async def _container_section(db: AsyncSession, name: str, registered_at: datetime, start: datetime, end: datetime,
                             kind: str, has_heartbeat: bool) -> dict:
    start = max(start, registered_at)       # container baru dihitung sejak terdaftar
    findings: list[rules.Finding] = []
    stats: dict = {}
    samples = await db.scalar(select(func.count()).where(
        Metric.container == name, Metric.name == "connections", Metric.at >= ingest.hour_start(start), Metric.at < end))
    if not samples:
        if has_heartbeat:
            findings.append(rules.Finding("warn", "Tidak ada metrik database pada periode ini"))
    else:
        peak = (await db.execute(
            select(func.max(Metric.peak), func.max(Metric.value)).where(
                Metric.container == name, Metric.name == "connections",
                Metric.at >= ingest.hour_start(start), Metric.at < end))).one()
        limit = await db.scalar(select(Metric.value).where(
            Metric.container == name, Metric.name == "max_connections", Metric.at < end).order_by(Metric.at.desc()).limit(1))
        refused = await _sum(db, name, "conn_refused", start, end)
        requests, reads = await _sum(db, name, "bp_requests", start, end), await _sum(db, name, "bp_reads", start, end)
        slow, deadlocks = await _sum(db, name, "slow_queries", start, end), await _sum(db, name, "deadlocks", start, end)
        ratio = rules.hit_ratio(requests, reads)
        stats.update(connections_peak=peak[0], max_connections=limit, refused=refused, hit_ratio=ratio,
                     slow_queries=slow, deadlocks=deadlocks)
        findings += rules.connection_findings(peak[0], limit, refused, settings.REPORT_CONN_WARN_PERCENT)
        findings += rules.performance_findings(
            ratio, slow, deadlocks, min_ratio=settings.REPORT_HITRATIO_MIN, max_slow=settings.REPORT_SLOW_QUERIES_MAX)

    series = (await disk_series(db, end, [name])).get(name, [])
    series = [p for p in series if p[0] < end]
    days = rules.forecast_full_days(series)
    if series:
        stats["disk_free"] = series[-1][1]
    stats["disk_full_days"] = days
    if finding := rules.disk_finding(days, settings.REPORT_DISK_BAD_DAYS, settings.REPORT_DISK_WARN_DAYS):
        findings.append(finding)

    sizes = list((await db.scalars(
        select(Backup.size).where(Backup.container == name, Backup.kind == "fullbackup", Backup.backup_at < end)
        .order_by(Backup.backup_at.desc()).limit(11))))
    if sizes:
        stats["full_size"] = sizes[0]
        if finding := rules.backup_size_finding(sizes[0], sizes[1:]):
            findings.append(finding)
    chain = await db.scalar(select(func.max(Backup.chain_id)).where(Backup.container == name, Backup.kind == "binlog"))
    if chain is not None:
        numbers = list(await db.scalars(select(Backup.binlog_no).where(
            Backup.container == name, Backup.kind == "binlog", Backup.chain_id == chain, Backup.binlog_no.is_not(None))))
        gaps = rules.missing_numbers(numbers)
        if gaps:
            findings.append(rules.Finding("warn", f"Rantai binlog putus: {len(gaps)} nomor hilang"))
    if kind == "weekly":
        stats["growth"] = await _growth(db, name, start, end)
    return {"name": name, "findings": [f.as_dict() for f in findings], "stats": stats}


async def _growth(db: AsyncSession, name: str, start: datetime, end: datetime) -> list[dict]:
    """Pertumbuhan ukuran tiap database selama periode: nilai terakhir dikurangi nilai pertama."""
    rows = (await db.execute(
        select(Metric.name, Metric.at, Metric.value).where(
            Metric.container == name, Metric.name.like(f"{ingest.SIZE_DB}%"), Metric.at >= ingest.hour_start(start),
            Metric.at < end).order_by(Metric.name, Metric.at))).all()
    by_db: dict[str, list[float]] = {}
    for row in rows:
        by_db.setdefault(row.name[len(ingest.SIZE_DB):], []).append(row.value)
    growth = [{"database": db_name, "bytes": values[-1], "delta": values[-1] - values[0]}
              for db_name, values in by_db.items()]
    return sorted(growth, key=lambda g: g["delta"], reverse=True)


async def build(db: AsyncSession, kind: str, start: datetime, end: datetime) -> dict:
    entries = registry.entries()
    heartbeats = set(await db.scalars(select(Heartbeat.container)))
    sections = []
    rows = (await db.execute(select(Container.name, Container.registered_at).where(Container.registered_at < end)
                             .order_by(Container.name))).all()
    for row in rows:
        if row.name not in entries.active:
            continue
        sections.append(await _container_section(db, row.name, row.registered_at, start, end, kind,
                                                 row.name in heartbeats))
    if kind == "daily":
        shown = [s for s in sections if s["findings"]]
    else:
        shown = sections
    return {"kind": kind, "start": start.isoformat(), "end": end.isoformat(),
            "checked": len(sections), "containers": shown}


def summary_text(report: Report) -> str:
    data = report.data
    label = "harian" if report.kind == "daily" else "mingguan"
    day = report.period_start.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE)).strftime("%d-%m-%Y")
    problems = [s for s in data["containers"] if s["findings"]]
    if not problems:
        return f"Laporan {label} {day}: tidak ada masalah di {data['checked']} container."
    lines = [f"Laporan {label} {day}: {len(problems)} dari {data['checked']} container bermasalah."]
    for section in problems:
        for finding in section["findings"][:3]:
            lines.append(f"- {section['name']}: {finding['text']}")
    return "\n".join(lines)[:3500]


async def publish_due(db: AsyncSession, client: httpx.AsyncClient) -> list[int]:
    """Menerbitkan laporan yang sudah jatuh tempo dan belum ada. Hanya periode terbaru; tidak mengisi yang terlewat."""
    async with db.begin():
        now = await db.scalar(select(func.now()))
    published = []
    for kind, start, end in due_periods(now):
        try:
            async with db.begin():
                if await db.scalar(select(Report.id).where(Report.kind == kind, Report.period_start == start)):
                    continue
                report = Report(kind=kind, period_start=start, period_end=end, data=await build(db, kind, start, end))
                db.add(report)
                await db.flush()
        except IntegrityError:      # instance lain menerbitkannya lebih dulu
            continue
        published.append(report.id)
        sent = await telegram.send_message(client, settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID,
                                           summary_text(report), api_base=settings.TELEGRAM_API_BASE)
        if sent:
            async with db.begin():
                await db.execute(update(Report).where(Report.id == report.id).values(sent_at=func.now()))
    return published
