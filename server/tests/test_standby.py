"""Database cadangan di server: pemilihan file, hasil runner, penjadwalan, dan pengaman penerapan binlog."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.agents.dependencies import registry as agents
from app.containers.models import Container
from app.containers.service import ensure_row
from app.core.database import async_session_factory
from app.runners import scheduling, service, standby
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, JobFile, RestoreTest, Standby, StandbyRun
from tests.test_runners import clean_files, set_nas  # noqa: F401

NAME = "siak-db"
OLD = datetime(2026, 1, 1)


async def add_backup(kind, relpath, hours_ago, *, no=None, chain=1, start=None):
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, NAME)
        from app.backups.models import Backup
        row = Backup(container=NAME, kind=kind, relpath=relpath, stored_path=f"{kind}/{relpath}", size=10,
                     sha256="a" * 64, plain_sha256=relpath.ljust(64, "x"), backup_at=datetime(2026, 11, 3, 12) - timedelta(hours=hours_ago),
                     binlog_no=no, chain_id=chain, binlog_start=start)
        db.add(row)
        await db.flush()
        return row.id


@pytest.fixture
async def world(client):
    await set_nas()
    agents.add(NAME)
    runners.add("runner-1")
    full = await add_backup("fullbackup", "full-new", 5, chain=1, start="mysql-bin.000010")
    older = await add_backup("fullbackup", "full-old", 30, chain=0, start="mysql-bin.000001")
    logs = {n: await add_backup("binlog", f"b{n}", 4, no=n) for n in (9, 10, 11, 12)}
    return {"full": full, "older": older, "logs": logs}


async def files_of(job_id):
    async with async_session_factory() as db:
        return set(await db.scalars(select(JobFile.backup_id).where(JobFile.job_id == job_id)))


async def full_job():
    async with async_session_factory() as db:
        return await standby.create_full_job(db, NAME, "runner-1", requested_by="alice")


async def test_full_job_has_newest_full_and_chain_from_binlog_start(world):
    job = await full_job()
    assert await files_of(job.id) == {world["full"], world["logs"][10], world["logs"][11], world["logs"][12]}
    assert job.kind == "standby_full" and job.requested_by == "alice"


async def test_failed_tested_full_is_skipped(world):
    async with async_session_factory() as db, db.begin():
        bad = Job(runner="runner-1", container=NAME, kind="restore_test", status="Gagal")
        db.add(bad)
        await db.flush()
        db.add(RestoreTest(job_id=bad.id, container=NAME, runner="runner-1", kind="full", backup_id=world["full"],
                           status="Gagal"))
    job = await full_job()
    assert world["full"] not in await files_of(job.id) and world["older"] in await files_of(job.id)


async def test_only_one_active_standby_job_per_container(world):
    await full_job()
    with pytest.raises(service.RunnerError) as err:
        await full_job()
    assert err.value.status_code == 409


async def test_no_full_means_no_job(client):
    await set_nas()
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, NAME)
    with pytest.raises(service.RunnerError):
        await full_job()


async def finish(job, status="Lolos", **standby_fields):
    async with async_session_factory() as db:
        from app.runners.schemas import JobResultIn
        body = {"status": status, "standby": standby_fields or None}
        async with db.begin():
            row = await db.get(Job, job.id)
            row.status, row.runner = service.STATUS_RUNNING, "runner-1"
        await service.finish_job(db, "runner-1", job.id, JobResultIn.model_validate(body))


async def standby_row():
    async with async_session_factory() as db:
        return await db.get(Standby, NAME)


async def test_successful_full_records_state_and_history(world):
    job = await full_job()
    await finish(job, full_backup_id=world["full"], applied_binlog_no=12, port=33100)
    row = await standby_row()
    assert (row.full_backup_id, row.chain_id, row.applied_binlog_no, row.port, row.runner) == (world["full"], 1, 12, 33100, "runner-1")
    assert row.data_until == datetime(2026, 11, 3, 8) and row.restored_at is not None
    async with async_session_factory() as db:
        run, = (await db.scalars(select(StandbyRun))).all()
    assert (run.kind, run.manual, run.status) == ("full", True, "Lolos")


async def test_applied_number_defaults_to_before_binlog_start(world):
    job = await full_job()
    await finish(job, full_backup_id=world["full"], port=33100)
    assert (await standby_row()).applied_binlog_no == 9


async def test_result_cannot_claim_a_full_outside_the_job(world):
    job = await full_job()
    with pytest.raises(service.RunnerError) as err:
        await finish(job, full_backup_id=world["older"])
    assert err.value.status_code == 403 and await standby_row() is None


async def test_failed_full_leaves_existing_standby_alone(world):
    first = await full_job()
    await finish(first, full_backup_id=world["full"], applied_binlog_no=12, port=33100)
    second = await full_job()
    await finish(second, "Gagal")
    assert (await standby_row()).applied_binlog_no == 12


async def test_apply_job_takes_only_newer_binlogs_of_same_chain(world):
    first = await full_job()
    await finish(first, full_backup_id=world["full"], applied_binlog_no=11, port=33100)
    async with async_session_factory() as db:
        job = await standby.create_apply_job(db, NAME, "runner-1")
    assert await files_of(job.id) == {world["logs"][12]}
    other = await add_backup("binlog", "other-chain", 1, no=13, chain=7)       # rantai lain tidak ikut
    await finish(job, applied_binlog_no=12)
    row = await standby_row()
    assert row.applied_binlog_no == 12 and row.data_until == datetime(2026, 11, 3, 8)
    async with async_session_factory() as db:
        assert await standby.create_apply_job(db, NAME, "runner-1") is None       # tidak ada binlog baru
    assert other


async def test_apply_needs_existing_standby_on_same_runner(world):
    async with async_session_factory() as db:
        assert await standby.create_apply_job(db, NAME, "runner-1") is None
    first = await full_job()
    await finish(first, full_backup_id=world["full"], applied_binlog_no=10, port=33100)
    async with async_session_factory() as db:
        assert await standby.create_apply_job(db, NAME, "runner-2") is None


async def test_failed_apply_marks_standby_broken_until_next_full(world):
    first = await full_job()
    await finish(first, full_backup_id=world["full"], applied_binlog_no=10, port=33100)
    async with async_session_factory() as db:
        job = await standby.create_apply_job(db, NAME, "runner-1")
    await finish(job, "Gagal")
    assert (await standby_row()).applied_binlog_no is None
    async with async_session_factory() as db:
        assert await standby.create_apply_job(db, NAME, "runner-1") is None
    again = await full_job()
    await finish(again, full_backup_id=world["full"], applied_binlog_no=12, port=33100)
    assert (await standby_row()).applied_binlog_no == 12


async def test_not_run_apply_keeps_position(world):
    first = await full_job()
    await finish(first, full_backup_id=world["full"], applied_binlog_no=10, port=33100)
    async with async_session_factory() as db:
        job = await standby.create_apply_job(db, NAME, "runner-1")
    await finish(job, "Tidak terlaksana")
    assert (await standby_row()).applied_binlog_no == 10


async def test_claim_carries_standby_context(client, world):
    first = await full_job()
    await finish(first, full_backup_id=world["full"], applied_binlog_no=11, port=33100)
    async with async_session_factory() as db:
        await standby.create_apply_job(db, NAME, "runner-1")
    resp = await client.post("/api/runner/jobs/claim", headers={"X-Runner-Token": runners.entries().active["runner-1"]})
    job = resp.json()["job"]
    assert job["kind"] == "standby_apply" and job["standby"] == {"applied_binlog_no": 11, "port": 33100}
    assert [f["binlog_no"] for f in job["files"]] == [12]


# ---------- penjadwalan ----------
async def enable(**over):
    async with async_session_factory() as db, db.begin():
        (await db.get(Container, NAME)).registered_at = OLD
        db.add(ContainerSettings(container=NAME, standby_runner="runner-1", standby_enabled=True, **over))


async def standby_jobs():
    async with async_session_factory() as db:
        return [j.kind for j in await db.scalars(select(Job).where(Job.kind.in_(standby.KINDS)).order_by(Job.id))]


async def schedule():
    async with async_session_factory() as db:
        return await scheduling.schedule_standby(db)


async def test_first_full_is_created_immediately_and_only_once(world):
    await enable()
    assert len(await schedule()) == 1
    assert await schedule() == []
    assert await standby_jobs() == ["standby_full"]


async def test_disabled_or_inactive_runner_creates_nothing(world):
    await enable()
    async with async_session_factory() as db, db.begin():
        (await db.get(ContainerSettings, NAME)).standby_enabled = False
    assert await schedule() == []


async def test_binlog_apply_follows_its_own_schedule(world):
    await enable(standby_binlog_schedule="* * * * *", standby_full_schedule="0 0 1 1 *")
    first = (await schedule())[0]
    async with async_session_factory() as db:
        job = await db.get(Job, first)
    await finish(job, full_backup_id=world["full"], applied_binlog_no=10, port=33100)
    async with async_session_factory() as db, db.begin():
        await db.execute(Job.__table__.update().values(created_at=datetime(2026, 11, 1)))
    assert len(await schedule()) == 1
    assert await standby_jobs() == ["standby_full", "standby_apply"]
