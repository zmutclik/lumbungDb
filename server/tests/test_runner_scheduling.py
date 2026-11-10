"""Jadwal uji restore dan alert runner."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.agents.dependencies import registry as agents
from app.alerts import service as alert_service
from app.containers.models import Container, Heartbeat
from app.containers.service import ensure_row
from app.core.config import settings
from app.core.database import async_session_factory
from app.runners import scheduling
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, RunnerHeartbeat
from tests.conftest import AGE
from tests.test_runners import clean_files  # noqa: F401

NAME = "siak-db"


@pytest.fixture
async def world(client):
    from tests.test_runners import set_nas
    await set_nas()
    token = agents.add(NAME)
    runners.add("runner-1")
    resp = await client.put("/api/upload/fullbackup/20261030_020000.xbstream.gz.age", content=AGE + b"isi" * 100,
                            headers={"X-Token": token, "X-Binlog-Start": "mysql-bin.000001"})
    assert resp.status_code == 200, resp.text
    async with async_session_factory() as db, db.begin():
        (await db.get(Container, NAME)).registered_at = datetime(2026, 1, 1)
        db.add(ContainerSettings(container=NAME, test_runner="runner-1", standby_runner="runner-1",
                                 test_schedule="* * * * *", test_full=True))
    return token


async def jobs():
    async with async_session_factory() as db:
        return (await db.scalars(select(Job).order_by(Job.id))).all()


async def run_schedule():
    async with async_session_factory() as db:
        return await scheduling.schedule_due_tests(db)


async def test_due_schedule_creates_one_job_and_waits_for_it(world):
    assert len(await run_schedule()) == 1
    assert await run_schedule() == []        # tugas pertama belum selesai
    assert [j.status for j in await jobs()] == ["Menunggu"]


async def test_schedule_not_due_or_off_creates_nothing(world):
    async with async_session_factory() as db, db.begin():
        await db.execute(update(ContainerSettings).values(test_schedule="off"))
    assert await run_schedule() == []
    async with async_session_factory() as db, db.begin():
        await db.execute(update(ContainerSettings).values(test_schedule="0 0 1 1 *"))
        await db.execute(update(Container).values(registered_at=func.now()))
    assert await run_schedule() == []


async def test_inactive_runner_or_container_is_skipped(world):
    agents.path.write_text(f"# {NAME} = " + "x" * 20 + "\n")
    assert await run_schedule() == []


async def test_container_without_files_is_skipped_quietly(world):
    async with async_session_factory() as db, db.begin():
        db.add(Container(name="kosong-db", registered_at=datetime(2026, 1, 1)))
        db.add(ContainerSettings(container="kosong-db", test_runner="runner-1", test_schedule="* * * * *"))
    agents.add("kosong-db")
    assert len(await run_schedule()) == 1       # hanya siak-db; kosong-db tidak punya file


async def test_defaults_assigned_only_when_default_runner_set(world, monkeypatch):
    async with async_session_factory() as db, db.begin():
        await db.execute(ContainerSettings.__table__.delete())
    async with async_session_factory() as db:
        await scheduling.ensure_defaults(db)
    async with async_session_factory() as db:
        assert (await db.scalars(select(ContainerSettings))).all() == []
    monkeypatch.setattr(settings, "DEFAULT_RUNNER", "runner-1")
    async with async_session_factory() as db:
        await scheduling.ensure_defaults(db)
    async with async_session_factory() as db:
        row, = (await db.scalars(select(ContainerSettings))).all()
    assert (row.test_runner, row.test_schedule) == ("runner-1", settings.RESTORE_TEST_DEFAULT)


async def alerts():
    async with async_session_factory() as db:
        return {a.key: a for a in await scheduling.runner_alerts(db)}


async def beat(runner="runner-1", machine="b" * 32, age_minutes=0):
    async with async_session_factory() as db, db.begin():
        db.add(RunnerHeartbeat(runner=runner, machine_id=machine, at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=age_minutes)))


async def test_runner_alerts_missing_then_stale_then_healthy(world):
    assert "runner:runner-1" in await alerts()
    await beat(age_minutes=30)
    assert "tidak mengirim heartbeat 30 menit" in (await alerts())["runner:runner-1"].cond
    async with async_session_factory() as db, db.begin():
        await db.execute(update(RunnerHeartbeat).values(at=func.now()))
    assert "runner:runner-1" not in await alerts()


async def finish(status, note=None):
    async with async_session_factory() as db, db.begin():
        db.add(Job(runner="runner-1", container=NAME, kind="restore_test", status=status, note=note))


async def test_failed_test_alerts_until_next_passes(world):
    await beat()
    await finish("Gagal", "prepare gagal")
    assert (await alerts())[f"restoretest:{NAME}"].detail == "prepare gagal"
    await finish("Tidak terlaksana", "unduhan gagal")           # tidak menghapus alert uji gagal? terbaru menentukan
    found = await alerts()
    assert f"restoretest:{NAME}" not in found and f"restorerun:{NAME}" in found
    await finish("Lolos")
    found = await alerts()
    assert f"restoretest:{NAME}" not in found and f"restorerun:{NAME}" not in found


async def test_same_server_raises_alert(world):
    await beat(machine="c" * 32)
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, NAME)
        db.add(Heartbeat(container=NAME, server="s", machine_id="c" * 32, info={}))
    assert f"restoreban:{NAME}" in await alerts()


async def test_runner_alerts_reach_dashboard_alert_list(world):
    async with async_session_factory() as db:
        _, _, items, _, _ = await alert_service.current_alerts(db)
    assert any(a.key == "runner:runner-1" for a in items)
