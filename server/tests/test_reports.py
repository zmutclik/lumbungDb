"""Tahap 3: metrik dari heartbeat, laporan harian dan mingguan, dan alert perkiraan disk."""
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.agents.dependencies import registry
from app.alerts import service as alert_service
from app.backups.models import Backup
from app.containers.service import ensure_row
from app.containers.models import Container, Heartbeat
from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.reports import rules
from app.reports import service as reports
from app.reports.models import Metric, MetricCounter, Report
from tests.test_control import payload

NAME = "siak-db"


def metrics(connections=10, uptime=1000, refused=0, requests=1000, reads=10, slow=0, deadlocks=0, sizes=None,
            max_connections=100):
    data = {
        "status": {
            "Threads_connected": connections, "Uptime": uptime, "Connection_errors_max_connections": refused,
            "Innodb_buffer_pool_read_requests": requests, "Innodb_buffer_pool_reads": reads,
            "Slow_queries": slow, "Innodb_deadlocks": deadlocks,
        },
        "max_connections": max_connections, "performance_schema": True,
    }
    if sizes:
        data["sizes"] = sizes
    return data


@pytest.fixture
async def agent():
    token = registry.add(NAME)
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))
    return {"X-Token": token}


async def beat(client, agent, **overrides):
    resp = await client.post("/api/heartbeat", json=payload(**overrides), headers=agent)
    assert resp.status_code == 200, resp.text


async def metric(name, container=NAME):
    async with async_session_factory() as db:
        return (await db.execute(select(Metric).where(Metric.container == container, Metric.name == name))).scalars().all()


# ---------- aturan murni ----------
def test_hit_ratio():
    assert rules.hit_ratio(0, 0) is None
    assert rules.hit_ratio(1000, 50) == pytest.approx(95.0)


def test_forecast_needs_shrinking_free_space_and_enough_data():
    start = datetime(2026, 10, 1)
    shrinking = [(start + timedelta(days=d), 100.0 - 10 * d) for d in range(5)]      # sisa 60 saat hari ke-4, -10/hari
    assert rules.forecast_full_days(shrinking) == pytest.approx(6.0)
    assert rules.forecast_full_days([(start, 5.0), (start + timedelta(days=1), 5.0), (start + timedelta(days=2), 5.0)]) is None
    growing = [(start + timedelta(days=d), 50.0 + d) for d in range(5)]
    assert rules.forecast_full_days(growing) is None
    assert rules.forecast_full_days(shrinking[:2]) is None
    assert rules.forecast_full_days([(start + timedelta(hours=h), 100.0 - h) for h in range(5)]) is None   # kurang dari 24 jam


def test_missing_numbers_and_size_finding():
    assert rules.missing_numbers([1, 2, 5]) == [3, 4]
    assert rules.missing_numbers([7]) == []
    assert rules.backup_size_finding(40, [100, 100, 100]).level == "warn"
    assert rules.backup_size_finding(100, [100, 100]) is None       # pembanding kurang
    assert rules.backup_size_finding(100, [100, 90, 110]) is None


def test_due_periods(monkeypatch):
    monkeypatch.setattr(reports.settings, "DISPLAY_TIMEZONE", "Asia/Jakarta")
    monkeypatch.setattr(reports.settings, "REPORT_HOUR", 7)
    # Senin 2 Nov 2026 07:30 WIB = 00:30 UTC
    kinds = [kind for kind, _, _ in reports.due_periods(datetime(2026, 11, 2, 0, 30))]
    assert kinds == ["daily", "weekly"]
    assert reports.due_periods(datetime(2026, 11, 1, 22, 0)) == []        # 05:00 WIB, belum jamnya
    daily = reports.due_periods(datetime(2026, 11, 3, 0, 30))               # Selasa
    assert [k for k, _, _ in daily] == ["daily"]
    assert daily[0][1:] == (datetime(2026, 11, 1, 17, 0), datetime(2026, 11, 2, 17, 0))


# ---------- ingest ----------
async def test_heartbeat_stores_gauges_with_peak(client, agent):
    await beat(client, agent, metrics=metrics(connections=10))
    await beat(client, agent, metrics=metrics(connections=40))
    await beat(client, agent, metrics=metrics(connections=5))
    row, = await metric("connections")
    assert (row.value, row.peak) == (5, 40)
    assert row.peak_at is not None
    assert (await metric("max_connections"))[0].value == 100
    assert (await metric("backup_disk_free"))[0].value == 900


async def test_counters_use_deltas_and_survive_restart(client, agent):
    await beat(client, agent, metrics=metrics(uptime=100, slow=5))          # acuan, belum dihitung
    assert await metric("slow_queries") == []
    await beat(client, agent, metrics=metrics(uptime=160, slow=8))
    await beat(client, agent, metrics=metrics(uptime=30, slow=2))           # MariaDB restart: mulai dari nol
    row, = await metric("slow_queries")
    assert row.value == 3 + 2
    async with async_session_factory() as db:
        assert (await db.get(MetricCounter, (NAME, "slow_queries"))).last_value == 2


async def test_sizes_are_stored_and_bad_payload_rejected(client, agent):
    sizes = {"databases": [{"name": "siak", "bytes": 5000}], "tables": [{"database": "siak", "table": "pasien", "bytes": 4000}]}
    await beat(client, agent, metrics=metrics(sizes=sizes))
    assert (await metric("size_db:siak"))[0].value == 5000
    assert (await metric("size_table:siak.pasien"))[0].value == 4000
    resp = await client.post("/api/heartbeat", json=payload(metrics={"status": "x"}), headers=agent)
    assert resp.status_code == 422


async def test_heartbeat_without_metrics_still_works(client, agent):
    await beat(client, agent)
    assert await metric("connections") == []


# ---------- laporan ----------
async def add_metric(name, at, value, peak=None, container=NAME):
    async with async_session_factory() as db, db.begin():
        db.add(Metric(container=container, at=at, name=name, value=value, peak=peak if peak is not None else value))


async def register(registered_at=None, name=NAME):
    registry.add(name)
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, name)
        if registered_at:
            (await db.get(Container, name)).registered_at = registered_at


async def build(kind, start, end):
    async with async_session_factory() as db, db.begin():
        return await reports.build(db, kind, start, end)


DAY = datetime(2026, 11, 1)
NEXT = DAY + timedelta(days=1)


async def test_daily_report_lists_only_containers_with_problems():
    await register(DAY - timedelta(days=30))
    await register(DAY - timedelta(days=30), "sehat-db")
    at = DAY + timedelta(hours=3)
    await add_metric("connections", at, 20, peak=96)
    await add_metric("max_connections", at, 100)
    await add_metric("conn_refused", at, 4)
    await add_metric("bp_requests", at, 10000)
    await add_metric("bp_reads", at, 2000)
    await add_metric("deadlocks", at, 2)
    for name, value in (("connections", 3), ("max_connections", 100)):
        await add_metric(name, at, value, container="sehat-db")
    data = await build("daily", DAY, NEXT)
    assert data["checked"] == 2
    section, = data["containers"]
    assert section["name"] == NAME
    texts = " | ".join(f["text"] for f in section["findings"])
    assert "Koneksi puncak 96%" in texts and "4 koneksi ditolak" in texts
    assert "Hit ratio buffer pool 80.0%" in texts and "2 deadlock" in texts
    assert {f["level"] for f in section["findings"]} == {"bad", "warn"}


async def test_report_counts_only_since_registration():
    await register(DAY + timedelta(hours=12))
    await add_metric("connections", DAY + timedelta(hours=2), 1, peak=99)       # sebelum terdaftar
    await add_metric("max_connections", DAY + timedelta(hours=2), 100)
    data = await build("daily", DAY, NEXT)
    assert data["containers"] == []        # tidak ada heartbeat, jadi tidak ada temuan; koneksi 99% sebelum terdaftar tidak dihitung
    section = {"findings": []}
    assert all("Koneksi puncak" not in f["text"] for f in section["findings"])


async def test_missing_metrics_flagged_when_agent_sends_heartbeats(client, agent):
    await beat(client, agent)       # heartbeat tanpa metrik, mis. user DB `lumbung` belum dibuat
    async with async_session_factory() as db:
        now = await db.scalar(select(func.now()))
    data = await build("daily", now - timedelta(days=1), now + timedelta(hours=1))
    assert "Tidak ada metrik" in data["containers"][0]["findings"][0]["text"]


async def test_weekly_report_has_trend_and_growth():
    await register(DAY - timedelta(days=30))
    for day in range(7):
        at = DAY + timedelta(days=day, hours=1)
        await add_metric("connections", at, 5)
        await add_metric("max_connections", at, 100)
        await add_metric("size_db:siak", at, 1000 + 100 * day)
    data = await build("weekly", DAY, DAY + timedelta(days=7))
    section, = data["containers"]
    assert section["stats"]["growth"] == [{"database": "siak", "bytes": 1600, "delta": 600}]


async def test_backup_size_drop_and_binlog_gap_findings():
    await register(DAY - timedelta(days=30))
    async with async_session_factory() as db, db.begin():
        for i, size in enumerate([1000, 1000, 1000, 1000, 300]):
            db.add(Backup(container=NAME, kind="fullbackup", relpath=f"f{i}", stored_path=f"f{i}", size=size,
                          sha256="a" * 64, plain_sha256=f"{i}" * 64, backup_at=DAY - timedelta(days=5 - i)))
        for n in (1, 2, 5):
            db.add(Backup(container=NAME, kind="binlog", relpath=f"b{n}", stored_path=f"b{n}", size=1, sha256="b" * 64,
                          plain_sha256=f"{n}" * 64, backup_at=DAY - timedelta(hours=n), binlog_no=n, chain_id=1))
    data = await build("daily", DAY, NEXT)
    texts = [f["text"] for f in data["containers"][0]["findings"]]
    assert any("Ukuran full terakhir hanya 30%" in t for t in texts)
    assert any("Rantai binlog putus: 2 nomor hilang" in t for t in texts)


async def test_publish_is_idempotent_and_sends_summary(monkeypatch):
    await register(DAY - timedelta(days=30))
    await add_metric("connections", DAY + timedelta(hours=1), 5, peak=97)
    await add_metric("max_connections", DAY + timedelta(hours=1), 100)
    monkeypatch.setattr(reports, "due_periods", lambda now: [("daily", DAY, NEXT)])
    monkeypatch.setattr(reports.settings, "TELEGRAM_TOKEN", "tok")
    monkeypatch.setattr(reports.settings, "TELEGRAM_CHAT_ID", "1")
    sent = []

    def handler(request: httpx.Request):
        sent.append(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with async_session_factory() as db:
            first = await reports.publish_due(db, http)
        async with async_session_factory() as db:
            second = await reports.publish_due(db, http)
    assert len(first) == 1 and second == []
    assert len(sent) == 1 and "Laporan harian" in sent[0] and "siak-db" in sent[0]
    async with async_session_factory() as db:
        report, = (await db.scalars(select(Report))).all()
    assert report.sent_at is not None and report.data["containers"][0]["name"] == NAME


async def test_report_pages(client):
    from tests.conftest import login, make_user
    await make_user()
    await login(client)
    await register(DAY - timedelta(days=30))
    async with async_session_factory() as db, db.begin():
        db.add(Report(kind="daily", period_start=DAY, period_end=NEXT, data={
            "kind": "daily", "checked": 1, "containers": [
                {"name": NAME, "findings": [{"level": "bad", "text": "2 deadlock <b>x</b>"}], "stats": {}}]}))
    page = await client.get("/reports")
    assert page.status_code == 200 and "1 dari 1" in page.text
    detail = await client.get("/reports/1")
    assert detail.status_code == 200 and "2 deadlock" in detail.text and "<b>x</b>" not in detail.text
    assert (await client.get("/reports/99")).status_code == 404


# ---------- alert perkiraan disk ----------
async def test_disk_forecast_raises_alert():
    await register(DAY - timedelta(days=30))
    async with async_session_factory() as db:
        now = await db.scalar(select(func.now()))
    top = now.replace(minute=0, second=0, microsecond=0)
    for day in range(10, -1, -1):
        await add_metric("backup_disk_free", top - timedelta(days=day), 100e9 - 10e9 * (10 - day) * 0.9)
    async with async_session_factory() as db, db.begin():
        db.add(Heartbeat(container=NAME, server="s", machine_id="a" * 64, info={}))
    async with async_session_factory() as db:
        _, _, alerts, _, _ = await alert_service.current_alerts(db)
    disk = [a for a in alerts if a.key == f"diskfull:{NAME}"]
    assert disk and disk[0].level == "bad" and "penuh dalam" in disk[0].cond
