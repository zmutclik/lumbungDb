"""Rekap harian untuk laporan bulanan sampai tahunan (app/reports/daily.py)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.containers.service import ensure_row
from app.core.database import async_session_factory
from app.reports import daily, ingest
from app.reports.models import DailyMetric, Metric, StatusSample

NAME = "siak-db"
DAY = date(2026, 11, 14)
START = datetime(2026, 11, 13, 17, 0)      # 14-11-2026 00:00 Asia/Jakarta dalam UTC


def sample(at, uptime, container=NAME, **values):
    data = {"Questions": 0, "Com_select": 0, "Threads_connected": 3, "Threads_running": 1, "max_connections": 151}
    data.update(values)
    return StatusSample(container=container, at=at, uptime=uptime, data=data)


def minute(m):
    return START + timedelta(minutes=m)


def test_day_bounds_follow_display_timezone():
    assert daily.day_bounds(DAY) == (START, START + timedelta(days=1))
    assert daily.local_day(START - timedelta(minutes=1)) == date(2026, 11, 13)
    assert daily.local_day(START) == DAY


def test_totals_peaks_and_averages():
    samples = [
        sample(minute(-1), 1000, Questions=100, cpu_seconds=10.0, cpus=2.0, rss_bytes=100),   # acuan dari kemarin
        sample(minute(0), 1060, Questions=700, Com_select=60, cpu_seconds=40.0, cpus=2.0, rss_bytes=300,
               Threads_connected=9),                                                            # 10 qps, 25% CPU
        sample(minute(1), 1120, Questions=820, Com_select=80, cpu_seconds=46.0, cpus=2.0, rss_bytes=200),
    ]
    out = daily.summarize(samples, START)
    assert out["minutes"] == (2, None) and out["restarts"] == (0, None)
    assert out["queries"][0] == 720 and out["select"][0] == 80
    assert out["qps_avg"][0] == pytest.approx(720 / 120)
    assert out["qps_peak"] == (pytest.approx(10), minute(0))
    assert out["cpu_avg"][0] == pytest.approx(36 / 240 * 100)
    assert out["cpu_peak"] == (pytest.approx(25), minute(0))
    assert out["rss_max"] == (300, minute(0)) and out["rss_avg"][0] == 250
    assert out["threads_max"] == (9, minute(0)) and out["threads_avg"][0] == 6
    assert out["max_connections"] == (151, None) and out["cpus"] == (2.0, None)
    assert "memory_limit" not in out


def test_restart_counts_from_zero_and_is_not_a_peak():
    samples = [
        sample(minute(0), 5000, Questions=9000),
        sample(minute(1), 30, Questions=300),                 # Uptime mundur: 300 kueri sejak restart
        sample(minute(2), 90, Questions=360),
        sample(minute(60), 200, Questions=500),               # celah 58 mnt, Uptime cuma +110 dtk: restart diam-diam
    ]
    out = daily.summarize(samples, START)
    assert out["restarts"] == (2, None)
    assert out["queries"][0] == 300 + 60 + 500
    assert out["qps_peak"] == (pytest.approx(1), minute(2))


def test_gap_without_restart_still_counts_totals_but_not_peak():
    samples = [sample(minute(0), 1000, Questions=0), sample(minute(30), 2800, Questions=18000)]
    out = daily.summarize(samples, START)
    assert out["queries"][0] == 18000 and out["qps_avg"][0] == pytest.approx(10)
    assert "qps_peak" not in out and out["restarts"] == (0, None)


async def test_rollup_day_writes_and_replaces_rows():
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, NAME)
        await ensure_row(db, "lain-db")
        db.add_all([
            sample(minute(-30), 1000, Questions=0),              # acuan lebih tua dari GAP, tetap dipakai
            sample(minute(0), 2800, Questions=1800),
            sample(minute(1440), 89200, Questions=99999),        # milik hari berikutnya
            sample(minute(5), 50, container="lain-db"),
        ])
        db.add_all([
            Metric(container=NAME, at=minute(0), name="deadlocks", value=2),
            Metric(container=NAME, at=minute(60), name="deadlocks", value=1),
            Metric(container=NAME, at=minute(0), name=ingest.SIZE_DB + "siak", value=100),
            Metric(container=NAME, at=minute(600), name=ingest.SIZE_DB + "siak", value=150),
            Metric(container=NAME, at=minute(600), name=ingest.SIZE_DB + "log", value=50),
            Metric(container=NAME, at=minute(120), name="backup_disk_free", value=700),
            Metric(container=NAME, at=minute(180), name="backup_disk_free", value=600),
            Metric(container=NAME, at=minute(-60), name="deadlocks", value=50),      # kemarin
            Metric(container=NAME, at=minute(0), name="sizeXdb:x", value=1),         # LIKE tanpa escape akan cocok
        ])
    async with async_session_factory() as db, db.begin():
        assert await daily.rollup_day(db, DAY) == 2
    async with async_session_factory() as db:
        rows = {r.name: r for r in (await db.execute(
            select(DailyMetric).where(DailyMetric.container == NAME, DailyMetric.day == DAY))).scalars()}
        assert rows["queries"].value == 1800 and rows["minutes"].value == 1
        assert rows["deadlocks"].value == 3
        assert rows["size_total"].value == 200 and rows["size_total"].peak_at == minute(600)
        assert rows["disk_free_min"].value == 600
        other = (await db.execute(select(DailyMetric).where(DailyMetric.container == "lain-db"))).scalars().all()
        assert {r.name for r in other} >= {"minutes", "threads_max"}

    async with async_session_factory() as db, db.begin():
        await db.execute(Metric.__table__.delete().where(Metric.name == "deadlocks"))
        await daily.rollup_day(db, DAY)
    async with async_session_factory() as db:
        names = set((await db.execute(select(DailyMetric.name).where(DailyMetric.container == NAME))).scalars())
        assert "deadlocks" not in names and "queries" in names


async def test_rollup_runs_for_yesterday_and_today_and_purge_keeps_recent_days():
    async with async_session_factory() as db:
        now = await db.scalar(select(daily.func.now()))
    today = daily.local_day(now)
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, NAME)
        db.add_all([sample(now - timedelta(minutes=2), 1000, Questions=0),
                    sample(now - timedelta(minutes=1), 1060, Questions=60)])
        db.add(DailyMetric(container=NAME, day=today - timedelta(days=900), name="queries", value=1))
        db.add(DailyMetric(container=NAME, day=today - timedelta(days=700), name="queries", value=1))
    async with async_session_factory() as db:
        await daily.rollup(db)
    async with async_session_factory() as db:
        await ingest.purge(db, now - timedelta(days=90), today - timedelta(days=800))
    async with async_session_factory() as db:
        days = set((await db.execute(select(DailyMetric.day).where(DailyMetric.name == "minutes"))).scalars())
        assert daily.local_day(now - timedelta(minutes=1)) in days
        kept = set((await db.execute(select(DailyMetric.day).where(DailyMetric.name == "queries"))).scalars())
        assert today - timedelta(days=900) not in kept and today - timedelta(days=700) in kept
