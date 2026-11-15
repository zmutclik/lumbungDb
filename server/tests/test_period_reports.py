"""Laporan bulanan, 3 bulanan, 6 bulanan, dan tahunan dari rekap harian (app/reports/period.py)."""
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.agents.dependencies import registry
from app.ai import service as ai_service
from app.containers.models import Container
from app.containers.service import ensure_row
from app.core.database import async_session_factory
from app.reports import period
from app.reports import service as reports
from app.reports.models import DailyMetric, Report
from tests.conftest import login, make_user

NAME = "siak-db"


def utc(day: date) -> datetime:
    return period.utc_midnight(day)


# ---------- periode ----------
@pytest.mark.parametrize("kind, today, expected", [
    ("monthly", date(2026, 11, 1), (date(2026, 10, 1), date(2026, 11, 1))),
    ("monthly", date(2026, 1, 5), (date(2025, 12, 1), date(2026, 1, 1))),
    ("quarterly", date(2026, 10, 1), (date(2026, 7, 1), date(2026, 10, 1))),
    ("quarterly", date(2026, 11, 15), (date(2026, 7, 1), date(2026, 10, 1))),
    ("quarterly", date(2026, 1, 1), (date(2025, 10, 1), date(2026, 1, 1))),
    ("semiannual", date(2026, 7, 3), (date(2026, 1, 1), date(2026, 7, 1))),
    ("semiannual", date(2026, 3, 3), (date(2025, 7, 1), date(2026, 1, 1))),
    ("yearly", date(2026, 11, 15), (date(2025, 1, 1), date(2026, 1, 1))),
])
def test_latest_period(kind, today, expected):
    assert period.latest_period(kind, today) == expected


def test_due_from_report_hour_on_the_first_until_a_week_late():
    def kinds(local: datetime) -> list[str]:
        now = local - timedelta(hours=7)      # Asia/Jakarta → UTC
        return [k for k, _, _ in period.due_periods(now)]

    assert kinds(datetime(2027, 1, 1, 6, 59)) == []
    assert kinds(datetime(2027, 1, 1, 7, 0)) == ["monthly", "quarterly", "semiannual", "yearly"]
    assert kinds(datetime(2026, 7, 8, 1, 0)) == ["monthly", "quarterly", "semiannual"]
    assert kinds(datetime(2026, 11, 3, 12, 0)) == ["monthly"]
    assert kinds(datetime(2026, 11, 9, 12, 0)) == []
    _, start, end = period.due_periods(datetime(2026, 11, 1, 1, 0))[0]
    assert (start, end) == (datetime(2026, 9, 30, 17, 0), datetime(2026, 10, 31, 17, 0))
    assert reports.due_periods(datetime(2026, 11, 1, 1, 0))[-1][0] == "monthly"


def test_period_label():
    assert period.period_label("monthly", utc(date(2026, 11, 1))) == "November 2026"
    assert period.period_label("quarterly", utc(date(2026, 10, 1))) == "Kuartal 4 2026"
    assert period.period_label("semiannual", utc(date(2026, 7, 1))) == "Semester 2 2026"
    assert period.period_label("yearly", utc(date(2026, 1, 1))) == "2026"


# ---------- agregasi ----------
def day_row(minutes=1440, **values):
    row = {"minutes": (minutes, None)}
    for key, value in values.items():
        row[key] = value if isinstance(value, tuple) else (value, None)
    return row


def test_aggregate_sums_weighted_averages_and_peaks():
    peak_at = datetime(2026, 10, 2, 3, 4)
    days = {
        date(2026, 10, 1): day_row(1440, queries=1000, cpu_avg=10, qps_peak=(5, None), size_total=100,
                                   bp_requests=1000, bp_reads=10, memory_limit=4096),
        date(2026, 10, 2): day_row(720, queries=3000, cpu_avg=40, qps_peak=(9, peak_at), size_total=160, restarts=1,
                                   bp_requests=1000, bp_reads=30, disk_free_min=50),
    }
    stats = period.aggregate(days, 4)
    assert stats["days"] == 2 and stats["coverage"] == pytest.approx(2160 / (4 * 1440) * 100, abs=0.1)
    assert stats["queries"] == 4000 and stats["restarts"] == 1
    assert stats["cpu_avg"] == pytest.approx((10 * 1440 + 40 * 720) / 2160)
    assert stats["qps_peak"] == 9 and stats["qps_peak_at"] == peak_at.isoformat()
    assert (stats["size_start"], stats["size_end"], stats["size_growth"]) == (100, 160, 60)
    assert stats["busiest_day"] == "2026-10-02" and stats["busiest_queries"] == 3000
    assert stats["hit_ratio"] == pytest.approx(98) and stats["memory_limit"] == 4096 and stats["disk_free_min"] == 50
    assert period.change({"queries": 4000, "cpu_avg": 20}, {"queries": 2000, "cpu_avg": 0}) == {"queries": 100}
    assert period.aggregate({}, 30) == {"days": 0, "period_days": 30, "coverage": 0.0, "hit_ratio": None}


def test_findings():
    base = period.aggregate({date(2026, 10, 1): day_row(1440, cpu_avg=85, rss_max=(990, None), memory_limit=1000,
                                                         threads_max=(90, None), max_connections=100,
                                                         slow_queries=500, restarts=2, deadlocks=1)}, 2)
    texts = {f.text: f.level for f in period.period_findings(base)}
    assert texts["Metrik hanya mencakup 50% periode"] == "warn"
    assert texts["MariaDB restart 2 kali"] == "warn"
    assert texts["CPU rata-rata 85% dari core container"] == "warn"
    assert texts["Memori mariadbd mencapai 99% batas container"] == "bad"
    assert any(t.startswith("Koneksi puncak 90%") for t in texts)
    assert "Rata-rata 500 slow query per hari (batas 100)" in texts and "1 deadlock" in texts
    empty = period.period_findings(period.aggregate({}, 30))
    assert [f.text for f in empty] == ["Tidak ada rekap harian pada periode ini"]


# ---------- dari database ----------
async def register(name=NAME, registered_at=datetime(2026, 1, 1)):
    registry.add(name)
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, name)
        (await db.get(Container, name)).registered_at = registered_at


async def add_days(start: date, count: int, container=NAME, **values):
    async with async_session_factory() as db, db.begin():
        for n in range(count):
            day = start + timedelta(days=n)
            db.add(DailyMetric(container=container, day=day, name="minutes", value=1440))
            for key, value in values.items():
                db.add(DailyMetric(container=container, day=day, name=key, value=value))


async def test_quarterly_report_compares_previous_quarter_and_lists_months():
    await register()
    await add_days(date(2026, 4, 1), 91, queries=100, cpu_avg=10)        # kuartal 2
    await add_days(date(2026, 7, 1), 92, queries=150, cpu_avg=20)        # kuartal 3
    async with async_session_factory() as db, db.begin():
        data = await reports.build(db, "quarterly", utc(date(2026, 7, 1)), utc(date(2026, 10, 1)))
    stats = data["containers"][0]["stats"]
    assert stats["days"] == 92 and stats["coverage"] == 100 and stats["queries"] == 150 * 92
    assert stats["change"]["queries"] == pytest.approx((150 * 92 - 100 * 91) / (100 * 91) * 100)
    assert stats["change"]["cpu_avg"] == pytest.approx(100)
    assert [m["month"] for m in stats["by_month"]] == ["2026-07-01", "2026-08-01", "2026-09-01"]
    assert stats["by_month"][1]["queries"] == 150 * 31
    assert stats["restore_tests"] == {"passed": 0, "failed": 0, "skipped": 0}
    assert data["containers"][0]["findings"] == []


async def test_new_container_counts_from_registration_and_monthly_has_no_breakdown():
    await register(registered_at=datetime(2026, 10, 15, 3, 0))
    await add_days(date(2026, 10, 15), 17, queries=10)
    async with async_session_factory() as db, db.begin():
        data = await reports.build(db, "monthly", utc(date(2026, 10, 1)), utc(date(2026, 11, 1)))
    stats = data["containers"][0]["stats"]
    assert stats["period_days"] == 17 and stats["coverage"] == 100
    assert "by_month" not in stats and "change" not in stats


async def test_period_report_pages_summary_and_ai_input(client):
    await make_user()
    await login(client)
    await register()
    await add_days(date(2026, 10, 1), 31, queries=1000, cpu_avg=80, qps_peak=3)
    async with async_session_factory() as db, db.begin():
        start, end = utc(date(2026, 10, 1)), utc(date(2026, 11, 1))
        report = Report(kind="monthly", period_start=start, period_end=end,
                        data=await reports.build(db, "monthly", start, end))
        db.add(report)
    assert reports.summary_text(report).startswith("Laporan bulanan Oktober 2026: 1 dari 1 container bermasalah.")
    assert "CPU rata-rata 80%" in reports.summary_text(report)
    page = await client.get("/reports?jenis=monthly")
    assert page.status_code == 200 and "Oktober 2026" in page.text and "Tahunan" in page.text
    detail = await client.get(f"/reports/{report.id}")
    assert detail.status_code == 200 and "Laporan bulanan Oktober 2026" in detail.text
    assert "31.000" in detail.text and "Cakupan metrik" in detail.text
    async with async_session_factory() as db:
        payload = await ai_service.build_input(db, await db.get(Report, report.id))
    assert payload["jenis"] == "bulanan" and payload["periode_mulai"] == "2026-10-01"
    stats = payload["container_bermasalah"][0]["stats"]
    assert stats["queries"] == 31000 and stats["cpu_avg"] == 80 and "by_month" not in stats


async def test_publish_due_period_report(monkeypatch):
    await register()
    await add_days(date(2026, 10, 1), 31, queries=5)
    start, end = utc(date(2026, 10, 1)), utc(date(2026, 11, 1))
    monkeypatch.setattr(reports, "due_periods", lambda now: [("monthly", start, end)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as http:
        async with async_session_factory() as db:
            assert len(await reports.publish_due(db, http)) == 1
    async with async_session_factory() as db:
        row = await db.scalar(select(Report).where(Report.kind == "monthly"))
    assert row.data["containers"][0]["stats"]["queries"] == 155
