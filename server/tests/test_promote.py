"""Naikkan ke production di server: pembuatan tugas, konteks untuk runner, dan pencatatan hasil."""
import pytest
from sqlalchemy import select

from app.agents.agents_file import NameRejected
from app.agents.dependencies import registry as agents
from app.backup_control.models import BackupRequest
from app.containers.models import Container
from app.core.database import async_session_factory
from app.runners import promote, service, standby
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, Promotion, RunnerHeartbeat, Standby, StandbyRun
from app.runners.schemas import JobResultIn, RunnerHeartbeatIn
from tests.test_runners import clean_files, set_nas  # noqa: F401
from tests.test_standby import NAME, finish, full_job, standby_row, world  # noqa: F401,F811

MACHINE_A, MACHINE_B, MACHINE_C = "a" * 32, "b" * 32, "c" * 32


async def beat(runner, machine):
    async with async_session_factory() as db:
        await service.record_heartbeat(db, runner, RunnerHeartbeatIn(server=runner, machine_id=machine))


@pytest.fixture
async def ready(world):  # noqa: F811
    """Database cadangan siap di runner-1; runner-2 di server lain."""
    runners.add("runner-2")
    await beat("runner-1", MACHINE_A)
    await beat("runner-2", MACHINE_B)
    job = await full_job()
    await finish(job, full_backup_id=world["full"], applied_binlog_no=12, port=33100)
    async with async_session_factory() as db, db.begin():
        db.add(ContainerSettings(container=NAME, test_runner="runner-1", standby_runner="runner-1", standby_enabled=True,
                                 test_schedule="30 3 * * 0", standby_full_schedule="0 2 * * *"))
    return world


async def create(new="siak-db-2", port=3306):
    async with async_session_factory() as db:
        return await promote.create(db, NAME, new, port, "alice")


async def finish_promote(job, status="Lolos"):
    async with async_session_factory() as db:
        async with db.begin():
            row = await db.get(Job, job.id)
            row.status = service.STATUS_RUNNING
        await service.finish_job(db, "runner-1", job.id, JobResultIn(status=status, note=None))


async def test_create_registers_new_name_and_job_for_standby_runner(ready):
    job = await create()
    assert (job.kind, job.runner, job.container, job.status) == ("standby_promote", "runner-1", NAME, "Menunggu")
    assert "siak-db-2" in agents.entries().active
    async with async_session_factory() as db:
        promotion = await db.get(Promotion, job.id)
        container = await db.get(Container, "siak-db-2")
    assert (promotion.new_name, promotion.port) == ("siak-db-2", 3306)
    assert (container.origin, container.source_container) == ("promote", NAME)


async def test_claim_gives_runner_token_from_agents_file_and_recipients(client, ready):
    await create()
    resp = await client.post("/api/runner/jobs/claim", headers={"X-Runner-Token": runners.entries().active["runner-1"]})
    context = resp.json()["job"]["standby"]["promote"]
    assert context["name"] == "siak-db-2" and context["port"] == 3306
    assert context["token"] == agents.entries().active["siak-db-2"] and context["age_recipients"].startswith("age1")
    assert resp.json()["job"]["files"] == []


async def test_without_standby_or_with_stopped_binlog_it_is_refused(world):  # noqa: F811
    with pytest.raises(service.RunnerError) as err:
        await create()
    assert err.value.status_code == 409 and "siak-db-2" not in agents.entries().active
    await beat("runner-1", MACHINE_A)
    job = await full_job()
    await finish(job, full_backup_id=world["full"], applied_binlog_no=12, port=33100)
    async with async_session_factory() as db, db.begin():
        (await db.get(Standby, NAME)).applied_binlog_no = None
    with pytest.raises(service.RunnerError) as err:
        await create()
    assert "terhenti" in err.value.detail


async def test_port_range_and_name_are_checked_without_leaving_traces(ready):
    with pytest.raises(service.RunnerError) as err:
        await create(port=80)
    assert err.value.status_code == 422
    with pytest.raises(NameRejected):
        await create(new=NAME)
    async with async_session_factory() as db:
        assert not (await db.scalars(select(Job).where(Job.kind == "standby_promote"))).all()


async def test_second_promotion_while_one_is_pending_is_refused(ready):
    await create()
    with pytest.raises(service.RunnerError) as err:
        await create(new="siak-db-3")
    assert err.value.status_code == 409 and "siak-db-3" not in agents.entries().active
    async with async_session_factory() as db:
        assert await standby.has_active_job(db, NAME)


async def test_inactive_runner_cannot_promote(ready):
    runners.disable("runner-1")
    with pytest.raises(service.RunnerError) as err:
        await create()
    assert "nonaktif" in err.value.detail


async def test_success_moves_standby_into_new_container(ready):
    job = await create()
    await finish_promote(job)
    async with async_session_factory() as db:
        new = await db.get(ContainerSettings, "siak-db-2")
        old = await db.get(ContainerSettings, NAME)
        first_full, = (await db.scalars(select(BackupRequest).where(BackupRequest.container == "siak-db-2"))).all()
        run = (await db.scalars(select(StandbyRun).where(StandbyRun.job_id == job.id))).one()
    assert await standby_row() is None
    assert (new.test_runner, new.standby_runner) == ("runner-2", "runner-2")      # server lain dari runner-1
    assert new.standby_enabled and new.standby_full_schedule == "0 2 * * *" and new.test_schedule == "30 3 * * 0"
    assert old.standby_enabled is False
    assert (first_full.kind, first_full.status) == ("full", "Menunggu")
    assert (run.kind, run.status) == ("naik", "Lolos")


async def test_runner_on_same_server_is_used_when_no_other(ready):
    await beat("runner-2", MACHINE_A)         # runner-2 ternyata satu mesin dengan runner-1
    job = await create()
    await finish_promote(job)
    async with async_session_factory() as db:
        new = await db.get(ContainerSettings, "siak-db-2")
    assert (new.test_runner, new.standby_runner) == ("runner-2", "runner-2")    # pemegangnya hanya bila tak ada lain


async def test_other_server_preferred_over_same_server(ready):
    runners.add("runner-3")
    await beat("runner-3", MACHINE_A)         # runner-3 satu mesin dengan runner-1; runner-2 di server lain
    async with async_session_factory() as db:
        assert await promote.pick_runner(db, "runner-1") == "runner-2"


async def test_healthy_runner_is_preferred_over_stale_one(ready):
    runners.add("runner-3")
    await beat("runner-3", MACHINE_C)
    async with async_session_factory() as db, db.begin():
        beat2 = await db.get(RunnerHeartbeat, "runner-2")
        beat2.at = beat2.at.replace(year=2020)
    async with async_session_factory() as db:
        assert await promote.pick_runner(db, "runner-1") == "runner-3"


@pytest.mark.parametrize("status", ["Gagal", "Tidak terlaksana"])
async def test_failure_withdraws_new_registration_and_keeps_standby(ready, status):
    job = await create()
    await finish_promote(job, status)
    assert "siak-db-2" not in agents.entries().all_names
    async with async_session_factory() as db:
        assert await db.get(Container, "siak-db-2") is None and await db.get(Promotion, job.id) is None
        assert (await db.get(Job, job.id)).status == status
    assert (await standby_row()).applied_binlog_no == 12
    async with async_session_factory() as db:
        assert not await standby.has_active_job(db, NAME)       # bisa dicoba lagi


async def test_failure_after_agent_connected_keeps_registration(ready):
    job = await create()
    async with async_session_factory() as db, db.begin():
        from app.containers.models import Heartbeat
        db.add(Heartbeat(container="siak-db-2", info={}))
    await finish_promote(job, "Gagal")
    assert "siak-db-2" in agents.entries().active
