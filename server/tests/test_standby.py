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


# ---------- alert ----------
async def alert_keys():
    async with async_session_factory() as db:
        return {a.key: a for a in await scheduling.runner_alerts(db)}


async def ready_standby(world, **cfg):
    await enable(**cfg)
    job = await full_job_without_actor()
    await finish(job, full_backup_id=world["full"], applied_binlog_no=12, port=33100)


async def full_job_without_actor():
    async with async_session_factory() as db:
        return await standby.create_full_job(db, NAME, "runner-1")


async def test_healthy_standby_has_no_standby_alert(world):
    await ready_standby(world)
    assert not [k for k in await alert_keys() if k.startswith("standby")]


async def test_broken_standby_alerts_until_next_full(world):
    await ready_standby(world)
    async with async_session_factory() as db:
        await db.execute(Standby.__table__.update().values(applied_binlog_no=None))
        await db.commit()
    found = await alert_keys()
    assert found[f"standby:{NAME}"].level == "bad" and "terhenti" in found[f"standby:{NAME}"].cond


async def test_failed_restore_alerts_and_recovers_after_success(world):
    await ready_standby(world)
    bad = await full_job_without_actor()
    await finish(bad, "Gagal")
    assert f"standbyrun:{NAME}" in await alert_keys()
    good = await full_job_without_actor()
    await finish(good, full_backup_id=world["full"], applied_binlog_no=12, port=33100)
    assert f"standbyrun:{NAME}" not in await alert_keys()


async def test_not_run_is_only_a_warning(world):
    await ready_standby(world)
    job = await full_job_without_actor()
    await finish(job, "Tidak terlaksana")
    assert (await alert_keys())[f"standbyrun:{NAME}"].level == "warn"


async def test_lag_when_old_binlogs_remain_unapplied(world):
    await ready_standby(world)
    assert f"standbylag:{NAME}" not in await alert_keys()
    await add_backup("binlog", "b13", 40, no=13)      # tanggal tetap di fixture, jadi jauh sebelum jam DB sekarang
    found = await alert_keys()
    assert "tertinggal" in found[f"standbylag:{NAME}"].cond and "jam dari binlog" in found[f"standbylag:{NAME}"].cond
    await add_backup("binlog", "b14", 0, no=14)       # binlog baru tidak menghapus alert yang sudah ada
    assert f"standbylag:{NAME}" in await alert_keys()


async def test_stale_full_and_disabled_standby(world):
    await ready_standby(world)
    async with async_session_factory() as db:
        await db.execute(Standby.__table__.update().values(restored_at=datetime(2026, 1, 1)))
        await db.commit()
    assert f"standbyfull:{NAME}" in await alert_keys()
    async with async_session_factory() as db, db.begin():
        (await db.get(ContainerSettings, NAME)).standby_enabled = False
    assert not [k for k in await alert_keys() if k.startswith("standby")]


async def test_runner_on_same_server_has_no_alert(world):
    from app.containers.models import Heartbeat
    from app.runners.models import RunnerHeartbeat
    await enable()
    async with async_session_factory() as db, db.begin():
        db.add(Heartbeat(container=NAME, server="s", machine_id="c" * 32, info={}))
        db.add(RunnerHeartbeat(runner="runner-1", machine_id="c" * 32))
    assert not [k for k in await alert_keys() if k.startswith("standbyban:")]


# ---------- penghapusan salinan ----------
async def kinds_by_runner():
    async with async_session_factory() as db:
        return sorted((j.runner, j.kind, j.status) for j in await db.scalars(select(Job).where(Job.kind == standby.KIND_REMOVE)))


async def finish_as(job, runner, body):
    from app.runners.schemas import JobResultIn
    async with async_session_factory() as db:
        async with db.begin():
            row = await db.get(Job, job.id)
            row.status, row.runner = service.STATUS_RUNNING, runner
        await service.finish_job(db, runner, job.id, JobResultIn.model_validate(body))


async def test_moving_to_another_runner_removes_the_old_copy_only_after_success(world):
    runners.add("runner-2")
    await ready_standby(world)
    async with async_session_factory() as db:
        job = await standby.create_full_job(db, NAME, "runner-2", requested_by="alice")
    assert await kinds_by_runner() == []                 # selama belum lolos, yang lama tetap
    await finish_as(job, "runner-2", {"status": "Gagal"})
    assert await kinds_by_runner() == [] and (await standby_row()).runner == "runner-1"
    async with async_session_factory() as db:
        job = await standby.create_full_job(db, NAME, "runner-2", requested_by="alice")
    await finish_as(job, "runner-2", {"status": "Lolos", "standby": {
        "full_backup_id": world["full"], "applied_binlog_no": 12, "port": 33101}})
    assert await kinds_by_runner() == [("runner-1", "standby_remove", "Menunggu")]
    assert (await standby_row()).runner == "runner-2"
    async with async_session_factory() as db:
        removal = (await db.scalars(select(Job).where(Job.kind == standby.KIND_REMOVE))).one()
    await finish_as(removal, "runner-1", {"status": "Lolos"})
    assert (await standby_row()).runner == "runner-2"      # baris milik runner baru tidak ikut terhapus
    async with async_session_factory() as db:
        run = (await db.scalars(select(StandbyRun).where(StandbyRun.kind == "hapus"))).one()
    assert run.runner == "runner-1" and run.status == "Lolos"


async def test_disabling_creates_a_removal_job_and_success_clears_the_state(world):
    await ready_standby(world)
    async with async_session_factory() as db:
        from app.runner_admin import service as admin
        await admin.save_standby_settings(db, NAME, enabled=False, runner="runner-1", full_schedule="0 3 * * *",
                                          binlog_schedule="0 * * * *")
    assert await kinds_by_runner() == [("runner-1", "standby_remove", "Menunggu")]
    async with async_session_factory() as db:
        from app.runner_admin import service as admin
        await admin.save_standby_settings(db, NAME, enabled=False, runner="runner-1", full_schedule="0 3 * * *",
                                          binlog_schedule="0 * * * *")
    assert len(await kinds_by_runner()) == 1             # disimpan dua kali tidak membuat dua tugas
    async with async_session_factory() as db:
        job = (await db.scalars(select(Job).where(Job.kind == standby.KIND_REMOVE))).one()
    await finish(job)
    assert await standby_row() is None


async def test_removal_job_does_not_block_a_new_restore(world):
    await ready_standby(world)
    async with async_session_factory() as db, db.begin():
        db.add(standby.remove_job(NAME, "runner-1"))
    assert (await full_job_without_actor()).kind == standby.KIND_FULL
