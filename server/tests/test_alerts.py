"""compute_alerts dengan jam palsu, dan pengecek dengan NAS palsu."""
import sys
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.alerts import service as alert_service
from app.alerts.models import Alert
from app.alerts.rules import ContainerState, compute_alerts, resolved_message, threshold_hours
from app.backup_control import cron
from app.core.config import settings
from app.core.database import async_session_factory
from app.nas.monitor import NasMonitor, ProbeResult
from app.nas.service import NasView, save_result
from app.agents.dependencies import registry

NOW = datetime(2026, 11, 2, 12, 0)
NAS_OK = NasView("ok", NOW, 1000, 800, False)
DAILY = {"full": "0 2 * * *", "dump": "0 3 * * *", "binlog": "*/15 * * * *"}


def state(**overrides) -> ContainerState:
    values = dict(
        name="a", active=True, registered_at=NOW - timedelta(days=30), heartbeat_at=NOW - timedelta(minutes=1),
        effective=dict(DAILY), counts={"fullbackup": 1, "databases": 2}, last_full=NOW - timedelta(hours=1),
        dumps={"db1": NOW - timedelta(hours=1), "_users": NOW - timedelta(hours=1)},
    )
    values.update(overrides)
    return ContainerState(**values)


def keys(states, nas=NAS_OK, now=NOW):
    return {a.key for a in compute_alerts(states, nas, now, settings)}


def test_healthy_container_has_no_alerts():
    assert keys([state()]) == set()


@pytest.mark.parametrize("nas_state,key", [("down", "nas"), ("hang", "nas"), ("low", "nas-low")])
def test_nas_alerts(nas_state, key):
    assert key in keys([], NasView(nas_state, NOW, 1000, 50, False))


def test_heartbeat_late():
    assert "heartbeat:a" in keys([state(heartbeat_at=NOW - timedelta(minutes=11))])
    assert "heartbeat:a" not in keys([state(heartbeat_at=NOW - timedelta(minutes=9))])


def test_new_container_alerts_until_first_backup():
    assert keys([state(heartbeat_at=None, counts={}, last_full=None, dumps={})]) == {"new:a"}
    assert "nobackup:a" in keys([state(counts={}, last_full=None, dumps={})])


def test_full_age_uses_reported_schedule():
    assert "full:a" in keys([state(last_full=NOW - timedelta(hours=37))])
    weekly = {**DAILY, "full": "0 2 * * 0"}
    assert threshold_hours(state(effective=weekly), "full", 36) == 252
    assert "full:a" not in keys([state(effective=weekly, last_full=NOW - timedelta(hours=200))])


def test_threshold_falls_back_until_schedule_reported():
    assert threshold_hours(state(effective=None), "full", 36) == 36
    assert threshold_hours(state(), "full", 99) == 36


def test_waiting_first_full_becomes_alert_after_36_hours():
    fresh = state(last_full=None, registered_at=NOW - timedelta(hours=35))
    assert "full:a" not in keys([fresh])
    assert "full:a" in keys([state(last_full=None, registered_at=NOW - timedelta(hours=37))])


def test_off_full_is_not_aged_but_alerts():
    found = keys([state(effective={**DAILY, "full": "off"}, last_full=NOW - timedelta(days=9))])
    assert "schedule-off:a" in found and "full:a" not in found


def test_off_dump_not_aged():
    found = keys([state(effective={**DAILY, "dump": "off"}, dumps={"db1": NOW - timedelta(days=9)})])
    assert not any(key.startswith("dump:") for key in found)


def test_dump_alert_per_database_and_users_separate():
    found = keys([state(dumps={"db1": NOW - timedelta(hours=1), "db2": NOW - timedelta(hours=40),
                               "_users": NOW - timedelta(hours=40)})])
    assert {"dump:a:db2", "dump:a:_users"} <= found and "dump:a:db1" not in found


def test_deleted_database_stops_being_monitored():
    old = state(dumps={"gone": NOW - timedelta(days=31)})
    assert "dump:a:gone" not in keys([old])
    message = resolved_message("dump:a:gone", "a: dump gone", {"a": old}, NOW, settings)
    assert "tidak lagi dipantau" in message and "Pulih" not in message


def test_disabled_container_has_no_alerts_and_says_not_monitored():
    off = state(active=False, heartbeat_at=NOW - timedelta(days=9))
    assert keys([off]) == set()
    assert "Tidak lagi dipantau" in resolved_message("heartbeat:a", "a: x", {"a": off}, NOW, settings)


def test_all_agents_on_one_server_become_one_alert():
    late = NOW - timedelta(minutes=30)
    found = keys([state(name="a", machine_id="m", server="db1", heartbeat_at=late),
                  state(name="b", machine_id="m", server="db1", heartbeat_at=late)])
    assert "server:m" in found and "heartbeat:a" not in found and "heartbeat:b" not in found
    partial = keys([state(name="a", machine_id="m", heartbeat_at=late), state(name="b", machine_id="m")])
    assert "heartbeat:a" in partial and "server:m" not in partial


def test_schedule_error_integrity_and_request_alerts():
    found = keys([state(schedule_error="ditolak", open_integrity=[(7, "x.gz")],
                        failed_requests=[(3, "Full", "Gagal")])])
    assert {"schedule-error:a", "integrity:7", "request:3"} <= found


def test_telegram_text_has_no_log_or_detail():
    alerts = compute_alerts([state(schedule_error="isi log rahasia")], NAS_OK, NOW, settings)
    assert all("rahasia" not in a.cond for a in alerts)


# ---------- cron ----------
@pytest.mark.parametrize("expr", ["0 2 * * *", "*/15 * * * *", "30 1 * * 1-5", "off", "0 */6 * * *", "0 0 1,15 * *"])
def test_cron_valid(expr):
    assert cron.is_valid(expr)


@pytest.mark.parametrize("expr", ["", "a b c d e", "0 2 * *", "61 2 * * *", "0 0 31 2 *", "0 2 * * * ; rm -rf /",
                                  "$(id) 2 * * *", "0  2 * * *"])
def test_cron_invalid(expr):
    assert not cron.is_valid(expr)


def test_cron_gap_and_label():
    assert cron.max_gap_hours("0 2 * * *") == 24
    assert cron.max_gap_hours("0 2 * * 1") == 168
    assert cron.label("0 2 * * *") == "Tiap hari 02:00"
    assert cron.label("0 2 * * 0") == "Tiap Minggu 02:00"
    assert cron.label("*/15 * * * *") == "Tiap 15 menit"
    assert cron.next_run("0 2 * * *", datetime(2026, 11, 2, 3, 0)) == datetime(2026, 11, 3, 2, 0)


# ---------- pengecek ----------
@pytest.fixture
def sent(monkeypatch):
    messages = []

    async def fake_send(client, token, chat_id, text, **kwargs):
        messages.append(text)
        return True

    from app.notifications import service as notification_service
    monkeypatch.setattr(notification_service.settings, "TELEGRAM_TOKEN", "tok")
    monkeypatch.setattr(notification_service.settings, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notification_service.telegram, "send_message", fake_send)
    return messages


async def run_check():
    async with async_session_factory() as db, httpx.AsyncClient() as client:
        await alert_service.run_check(db, client)


async def alert_rows():
    async with async_session_factory() as db:
        return {row.key: row for row in await db.scalars(select(Alert))}


async def test_notify_once_then_remind_then_recover(sent):
    from sqlalchemy import text
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("down"))
    await run_check()
    assert sent == ["NAS/NFS tidak ter-mount"]
    await run_check()
    assert len(sent) == 1                  # tidak berulang tiap menit
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE alerts SET last_sent = NOW() - INTERVAL 13 HOUR"))
    await run_check()
    assert sent[-1] == "Pengingat: NAS/NFS tidak ter-mount"
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))
    await run_check()
    assert sent[-1] == "Pulih: NAS/NFS tidak ter-mount"
    assert await alert_rows() == {}


async def test_new_container_alerts_from_registry(sent):
    from app.containers.service import sync_with_file
    registry.add("baru")
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))
        await sync_with_file(db)
    await run_check()
    assert "new:baru" in await alert_rows()


async def test_nas_since_only_changes_with_state():
    from sqlalchemy import text

    from app.nas.models import NasStatus
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE nas_status SET since = NOW() - INTERVAL 1 HOUR"))
    async with async_session_factory() as db:
        before = await db.scalar(select(NasStatus.since))
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 700))
    async with async_session_factory() as db:
        assert await db.scalar(select(NasStatus.since)) == before
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("hang"))
    async with async_session_factory() as db:
        row = await db.scalar(select(NasStatus))
    assert row.since > before and row.state == "hang" and row.free_bytes == 700   # angka terakhir dipertahankan


# ---------- pemeriksa NAS ----------
async def test_probe_detects_marker(tmp_path):
    assert (await NasMonitor(str(tmp_path), ".m", 5).check()).state == "down"
    (tmp_path / ".m").touch()
    result = await NasMonitor(str(tmp_path), ".m", 5).check()
    assert result.state in ("ok", "low") and result.total > 0


async def test_empty_marker_disables_check(tmp_path):
    assert (await NasMonitor(str(tmp_path), "", 5).check()).state in ("ok", "low")


async def test_hung_probe_is_not_restarted_and_recovers(tmp_path, monkeypatch):
    """Proses yang tersangkut: status hang, tidak dibuat proses baru, pulih saat proses itu selesai."""
    import subprocess

    from app.nas import monitor as monitor_module
    release = tmp_path / "lepas"
    script = tmp_path / "probe.py"
    script.write_text(
        "import json,os,sys,time\n"
        f"while not os.path.exists({str(release)!r}): time.sleep(0.02)\n"
        "print(json.dumps({'mounted': True, 'total': 100, 'free': 50}))\n"
    )
    monkeypatch.setattr(monitor_module, "PROBE", script)
    started = []
    real_popen = subprocess.Popen

    def counting_popen(*args, **kwargs):
        started.append(args)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(monitor_module.subprocess, "Popen", counting_popen)
    nas = NasMonitor(str(tmp_path), "", 0.1)
    assert (await nas.check()).state == "hang"
    assert (await nas.check()).state == "hang"
    assert len(started) == 1
    release.touch()
    nas.timeout = 5
    assert (await nas.check()).state == "ok"
    assert sys.executable in started[0][0]


async def test_container_removed_from_file_is_dropped_unless_it_has_history():
    from app.backups.models import Backup
    from app.containers.models import Container, Heartbeat
    from app.containers.service import sync_with_file
    for name in ("hapus", "pernah", "punya", "mati", "tetap"):
        registry.add(name)
    async with async_session_factory() as db:
        await sync_with_file(db)
    async with async_session_factory() as db, db.begin():
        db.add(Heartbeat(container="pernah", info={}))
        db.add(Backup(container="punya", kind="fullbackup", relpath="f", stored_path="f", size=1, sha256="a" * 64,
                      plain_sha256="b" * 64, backup_at=NOW))
    registry.disable("mati")
    text = registry.path.read_text()
    registry.path.write_text("".join(
        line for line in text.splitlines(keepends=True) if line.split("=")[0].strip() not in ("hapus", "pernah", "punya")
    ))
    async with async_session_factory() as db:
        await sync_with_file(db)
    async with async_session_factory() as db:
        names = set(await db.scalars(select(Container.name)))
    assert names == {"pernah", "punya", "mati", "tetap"}


async def test_missing_agents_file_drops_nothing():
    from app.containers.models import Container
    from app.containers.service import sync_with_file
    registry.add("ada")
    async with async_session_factory() as db:
        await sync_with_file(db)
    registry.path.unlink()
    async with async_session_factory() as db:
        await sync_with_file(db)
    async with async_session_factory() as db:
        assert set(await db.scalars(select(Container.name))) == {"ada"}
