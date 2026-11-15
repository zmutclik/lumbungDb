"""Laporan bulanan, 3 bulanan, 6 bulanan, dan tahunan dari rekap harian (daily_metrics).

Periode mengikuti kalender lokal (DISPLAY_TIMEZONE): bulan, kuartal (Jan-Mar, Apr-Jun, ...), semester (Jan-Jun,
Jul-Des), dan tahun. Laporan terbit pada hari pertama sesudah periode berakhir pada REPORT_HOUR; bila worker mati, masih
diterbitkan sampai PERIOD_LATE_DAYS sesudahnya, karena rekap hariannya tetap tersimpan. Tiap container dibandingkan
dengan periode sebelumnya yang sama panjang."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backups.models import Backup
from app.core.config import settings
from app.reports import rules
from app.reports.models import DailyMetric
from app.runners.models import RestoreTest

KINDS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}     # panjang periode dalam bulan
LABELS = {"monthly": "bulanan", "quarterly": "3 bulanan", "semiannual": "6 bulanan", "yearly": "tahunan"}
PERIOD_LATE_DAYS = 7
MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
          "November", "Desember")

SUMS = ("queries", "select", "insert", "update", "delete", "sessions", "bytes_in", "bytes_out", "slow_queries",
        "bp_requests", "bp_reads", "restarts", "deadlocks", "conn_refused")
WEIGHTED = ("qps_avg", "cpu_avg", "rss_avg", "threads_avg")        # rata-rata harian, berbobot menit tercakup
PEAKS = ("qps_peak", "cpu_peak", "rss_max", "threads_max", "running_max")
LAST = ("max_connections", "memory_limit", "cpus")
COMPARE = ("queries", "qps_avg", "cpu_avg", "rss_max", "slow_queries", "size_growth")


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def utc_midnight(day: date) -> datetime:
    local = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(settings.DISPLAY_TIMEZONE))
    return local.astimezone(UTC).replace(tzinfo=None)


def local_date(value: datetime) -> date:
    return value.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE)).date()


def latest_period(kind: str, today: date) -> tuple[date, date]:
    """Periode lengkap terakhir sebelum `today`: [awal, akhir) dalam tanggal lokal."""
    months = KINDS[kind]
    first = date(today.year, today.month, 1)
    end = add_months(date(first.year, 1, 1), (first.month - 1) // months * months)
    return add_months(end, -months), end


def due_periods(now: datetime) -> list[tuple[str, datetime, datetime]]:
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE))
    today = local.date()
    due = []
    for kind in KINDS:
        start, end = latest_period(kind, today)
        late = (today - end).days
        if late > PERIOD_LATE_DAYS or (late == 0 and local.hour < settings.REPORT_HOUR):
            continue
        due.append((kind, utc_midnight(start), utc_midnight(end)))
    return due


def period_label(kind: str, start: datetime) -> str:
    """"November 2026", "Kuartal 4 2026", "Semester 2 2026", "2026"."""
    day = local_date(start)
    if kind == "monthly":
        return f"{MONTHS[day.month - 1]} {day.year}"
    if kind == "quarterly":
        return f"Kuartal {(day.month - 1) // 3 + 1} {day.year}"
    if kind == "semiannual":
        return f"Semester {(day.month - 1) // 6 + 1} {day.year}"
    return str(day.year)


# ---------- agregasi ----------
Days = dict[date, dict[str, tuple[float, datetime | None]]]


def aggregate(days: Days, period_days: int) -> dict:
    """Rekap harian → angka satu periode. `period_days` adalah jumlah hari yang seharusnya punya data."""
    out: dict = {"days": len(days), "period_days": period_days}
    minutes = {d: v["minutes"][0] for d, v in days.items() if "minutes" in v}
    total_minutes = sum(minutes.values())
    out["coverage"] = round(total_minutes / (period_days * 1440) * 100, 1) if period_days else None
    for key in SUMS:
        values = [v[key][0] for v in days.values() if key in v]
        if values:
            out[key] = sum(values)
    for key in WEIGHTED:
        pairs = [(v[key][0], minutes.get(d, 0)) for d, v in days.items() if key in v]
        weight = sum(w for _, w in pairs)
        if weight:
            out[key] = sum(value * w for value, w in pairs) / weight
    for key in PEAKS:
        peaks = [v[key] for v in days.values() if key in v]
        if peaks:
            value, at = max(peaks, key=lambda p: p[0])
            out[key], out[f"{key}_at"] = value, at.isoformat() if at else None
    ordered = sorted(days)
    for key in LAST:
        last = next((days[d][key][0] for d in reversed(ordered) if key in days[d]), None)
        if last is not None:
            out[key] = last
    sizes = [days[d]["size_total"][0] for d in ordered if "size_total" in days[d]]
    if sizes:
        out["size_start"], out["size_end"] = sizes[0], sizes[-1]
        out["size_growth"] = sizes[-1] - sizes[0]
    free = [v["disk_free_min"][0] for v in days.values() if "disk_free_min" in v]
    if free:
        out["disk_free_min"] = min(free)
    busiest = [(v["queries"][0], d) for d, v in days.items() if "queries" in v]
    if busiest:
        value, day = max(busiest)
        out["busiest_day"], out["busiest_queries"] = day.isoformat(), value
    out["hit_ratio"] = rules.hit_ratio(out.get("bp_requests", 0), out.get("bp_reads", 0))
    return out


def change(current: dict, previous: dict) -> dict:
    """Persen perubahan terhadap periode sebelumnya; hanya bila keduanya ada dan pembandingnya bukan nol."""
    result = {}
    for key in COMPARE:
        now, before = current.get(key), previous.get(key)
        if now is not None and before:
            result[key] = (now - before) / abs(before) * 100
    return result


def monthly_rows(days: Days, start: date, end: date) -> list[dict]:
    """Rincian per bulan untuk laporan lebih dari satu bulan."""
    rows = []
    month = start
    while month < end:
        following = add_months(month, 1)
        subset = {d: v for d, v in days.items() if month <= d < following}
        stats = aggregate(subset, (following - month).days)
        rows.append({"month": month.isoformat(), **{k: stats.get(k) for k in (
            "coverage", "queries", "qps_avg", "cpu_avg", "cpu_peak", "rss_max", "size_end", "slow_queries",
            "restarts")}})
        month = following
    return rows


def period_findings(stats: dict) -> list[rules.Finding]:
    found = []
    if not stats["days"]:
        return [rules.Finding("warn", "Tidak ada rekap harian pada periode ini")]
    if stats["coverage"] is not None and stats["coverage"] < settings.REPORT_COVERAGE_MIN:
        found.append(rules.Finding("warn", f"Metrik hanya mencakup {stats['coverage']:.0f}% periode"))
    if stats.get("restarts"):
        found.append(rules.Finding("warn", f"MariaDB restart {stats['restarts']:.0f} kali"))
    cpu = stats.get("cpu_avg")
    if cpu is not None and cpu >= settings.REPORT_CPU_AVG_WARN:
        found.append(rules.Finding("warn", f"CPU rata-rata {cpu:.0f}% dari core container"))
    if stats.get("rss_max") and stats.get("memory_limit"):
        percent = stats["rss_max"] / stats["memory_limit"] * 100
        if percent >= settings.REPORT_MEMORY_WARN_PERCENT:
            found.append(rules.Finding("bad" if percent >= 98 else "warn",
                                       f"Memori mariadbd mencapai {percent:.0f}% batas container"))
    found += rules.connection_findings(stats.get("threads_max"), stats.get("max_connections"),
                                       stats.get("conn_refused", 0), settings.REPORT_CONN_WARN_PERCENT)
    slow_per_day = stats.get("slow_queries", 0) / max(stats["days"], 1)
    found += rules.performance_findings(stats.get("hit_ratio"), 0, stats.get("deadlocks", 0),
                                        min_ratio=settings.REPORT_HITRATIO_MIN, max_slow=0)
    if slow_per_day > settings.REPORT_SLOW_QUERIES_MAX:
        found.append(rules.Finding("warn", f"Rata-rata {slow_per_day:.0f} slow query per hari "
                                           f"(batas {settings.REPORT_SLOW_QUERIES_MAX})"))
    return found


@dataclass
class _Tests:
    passed: int = 0
    failed: int = 0
    skipped: int = 0


async def _load_days(db: AsyncSession, container: str, start: date, end: date) -> Days:
    days: Days = defaultdict(dict)
    rows = await db.execute(select(DailyMetric.day, DailyMetric.name, DailyMetric.value, DailyMetric.peak_at).where(
        DailyMetric.container == container, DailyMetric.day >= start, DailyMetric.day < end))
    for row in rows:
        days[row.day][row.name] = (row.value, row.peak_at)
    return dict(days)


async def _restore_tests(db: AsyncSession, container: str, start: datetime, end: datetime) -> _Tests:
    tests = _Tests()
    rows = await db.execute(select(RestoreTest.status, func.count()).where(
        RestoreTest.container == container, RestoreTest.at >= start, RestoreTest.at < end).group_by(RestoreTest.status))
    for status, n in rows:
        if status == "Lolos":
            tests.passed += n
        elif status == "Gagal":
            tests.failed += n
        else:
            tests.skipped += n
    return tests


async def section(db: AsyncSession, name: str, registered_at: datetime, kind: str, start: datetime,
                  end: datetime) -> dict:
    first, last = local_date(start), local_date(end)
    first_counted = max(first, local_date(registered_at))       # container baru dihitung sejak terdaftar
    months = KINDS[kind]
    prev_start = add_months(first, -months)
    days = await _load_days(db, name, prev_start, last)
    current = {d: v for d, v in days.items() if d >= first_counted}
    previous = {d: v for d, v in days.items() if d < first}
    stats = aggregate(current, max((last - first_counted).days, 0))
    findings = period_findings(stats)
    if previous:
        prev_stats = aggregate(previous, (first - prev_start).days)
        stats["previous"] = {k: prev_stats.get(k) for k in COMPARE}
        stats["change"] = change(stats, prev_stats)
    if months > 1:
        stats["by_month"] = monthly_rows(current, first, last)
    tests = await _restore_tests(db, name, start, end)
    stats["restore_tests"] = {"passed": tests.passed, "failed": tests.failed, "skipped": tests.skipped}
    if tests.failed:
        findings.append(rules.Finding("bad", f"{tests.failed} uji restore gagal"))
    full = await db.scalar(select(Backup.size).where(Backup.container == name, Backup.kind == "fullbackup",
                                                     Backup.backup_at < end).order_by(Backup.backup_at.desc()).limit(1))
    if full is not None:
        stats["full_size"] = full
    return {"name": name, "findings": [f.as_dict() for f in findings], "stats": stats}
