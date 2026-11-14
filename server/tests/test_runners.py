"""Runner: hanya bisa mengambil tugas dan file miliknya, dan tiap unduhan tercatat."""
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.agents.dependencies import registry as agents
from app.backups.models import Backup
from app.core.config import settings
from app.core.database import async_session_factory, db_now_plus
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.runners import service
from app.runners.dependencies import registry as runners
from app.runners.models import Job, JobDownload, RestoreTest, RunnerHeartbeat
from tests.conftest import AGE

DATA = Path(settings.DATA_DIR)
BODY = AGE + b"isi terenkripsi" * 1000


async def set_nas(state="ok"):
    async with async_session_factory() as db:
        await save_result(db, ProbeResult(state, 1000, 900))


@pytest.fixture(autouse=True)
def clean_files():
    yield
    for path in (Path(agents.path), Path(runners.path)):
        path.unlink(missing_ok=True)


@pytest.fixture
async def world(client):
    await set_nas()
    agent = {"X-Token": agents.add("siak-db")}
    resp = await client.put("/api/upload/fullbackup/20261030_020000.xbstream.gz.age", content=BODY,
                            headers={**agent, "X-Binlog-Start": "mysql-bin.000001"})
    assert resp.status_code == 200, resp.text
    resp = await client.put("/api/upload/fullbackup/20261029_020000.xbstream.gz.age", content=AGE + b"lama", headers=agent)
    assert resp.status_code == 200
    return {"agent": agent, "one": {"X-Runner-Token": runners.add("runner-1")},
            "two": {"X-Runner-Token": runners.add("runner-2")}}


async def new_job(runner="runner-1", **kwargs):
    async with async_session_factory() as db:
        return await service.create_restore_test(db, "siak-db", runner, **kwargs)


async def claimed(client, headers):
    resp = await client.post("/api/runner/jobs/claim", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["job"]


async def test_agent_token_cannot_act_as_runner(client, world):
    for call in (client.post("/api/runner/heartbeat", json={}, headers={"X-Runner-Token": world["agent"]["X-Token"]}),
                 client.post("/api/runner/jobs/claim", headers=world["agent"]),
                 client.post("/api/runner/jobs/claim")):
        assert (await call).status_code == 401


async def test_runner_token_cannot_act_as_agent(client, world):
    token = world["one"]["X-Runner-Token"]
    resp = await client.put("/api/upload/fullbackup/x.gz.age", content=AGE, headers={"X-Token": token})
    assert resp.status_code == 401


async def test_heartbeat_is_stored_and_updated(client, world):
    for free in (10, 20):
        resp = await client.post("/api/runner/heartbeat", headers=world["one"], json={
            "server": "vm-uji", "machine_id": "a" * 32, "version": "r1", "scratch_total": 100, "scratch_free": free})
        assert resp.status_code == 200
    async with async_session_factory() as db:
        rows = list(await db.scalars(select(RunnerHeartbeat)))
    assert [(r.runner, r.scratch_free) for r in rows] == [("runner-1", 20)]


async def test_claim_returns_only_own_job_with_newest_full(client, world):
    await new_job()
    assert await claimed(client, world["two"]) is None
    job = await claimed(client, world["one"])
    assert job["container"] == "siak-db"
    assert [f["relpath"] for f in job["files"]] == ["20261030_020000.xbstream.gz.age"]
    assert job["files"][0]["sha256"] == hashlib.sha256(BODY).hexdigest()


async def test_one_job_at_a_time_per_runner(client, world):
    await new_job()
    await new_job()
    assert await claimed(client, world["one"]) is not None
    assert await claimed(client, world["one"]) is None


async def test_total_limit_across_runners(client, world, monkeypatch):
    monkeypatch.setattr(settings, "RUNNER_MAX_TOTAL", 1)
    await new_job("runner-1")
    await new_job("runner-2")
    assert await claimed(client, world["one"]) is not None
    assert await claimed(client, world["two"]) is None


async def test_no_job_while_nas_has_a_problem(client, world):
    await new_job()
    await set_nas("down")
    assert await claimed(client, world["one"]) is None


async def test_runner_on_same_server_still_gets_restore_test(client, world):
    """Uji restore aman di server yang sama: container ujinya tanpa jaringan dan dibuang sesudahnya."""
    await client.post("/api/heartbeat", headers=world["agent"], json={"server": "db1", "machine_id": "b" * 32})
    await client.post("/api/runner/heartbeat", headers=world["one"], json={"machine_id": "b" * 32})
    job = await new_job()
    got = await claimed(client, world["one"])
    assert got is not None and got["id"] == job.id


async def test_download_full_and_resume_with_range(client, world):
    await new_job()
    job = await claimed(client, world["one"])
    url = f"/api/runner/jobs/{job['id']}/files/{job['files'][0]['id']}"
    whole = await client.get(url, headers=world["one"])
    assert whole.status_code == 200 and whole.content == BODY
    assert whole.headers["accept-ranges"] == "bytes"
    part = await client.get(url, headers={**world["one"], "Range": "bytes=100-"})
    assert part.status_code == 206 and part.content == BODY[100:]
    assert part.headers["content-range"] == f"bytes 100-{len(BODY) - 1}/{len(BODY)}"
    async with async_session_factory() as db:
        logged = list(await db.scalars(select(JobDownload).order_by(JobDownload.id)))
    assert [(d.runner, d.offset) for d in logged] == [("runner-1", 0), ("runner-1", 100)]


@pytest.mark.parametrize("header", ["bytes=0-10", "items=1-", "bytes=99999999-"])
async def test_unsupported_or_outside_range_rejected(client, world, header):
    await new_job()
    job = await claimed(client, world["one"])
    url = f"/api/runner/jobs/{job['id']}/files/{job['files'][0]['id']}"
    assert (await client.get(url, headers={**world["one"], "Range": header})).status_code == 416


async def test_download_forbidden_outside_own_running_job(client, world):
    await new_job()
    job = await claimed(client, world["one"])
    async with async_session_factory() as db:
        other_file = await db.scalar(select(Backup.id).where(Backup.relpath.like("20261029%")))
    file_id = job["files"][0]["id"]
    assert (await client.get(f"/api/runner/jobs/{job['id']}/files/{file_id}", headers=world["two"])).status_code == 403
    assert (await client.get(f"/api/runner/jobs/{job['id']}/files/{other_file}", headers=world["one"])).status_code == 403
    assert (await client.get(f"/api/runner/jobs/99999/files/{file_id}", headers=world["one"])).status_code == 403
    done = await client.post(f"/api/runner/jobs/{job['id']}/result", headers=world["one"], json={"status": "Lolos"})
    assert done.status_code == 200
    assert (await client.get(f"/api/runner/jobs/{job['id']}/files/{file_id}", headers=world["one"])).status_code == 403
    async with async_session_factory() as db:
        assert list(await db.scalars(select(JobDownload))) == []


async def test_download_refused_while_nas_has_a_problem(client, world):
    await new_job()
    job = await claimed(client, world["one"])
    await set_nas("hang")
    resp = await client.get(f"/api/runner/jobs/{job['id']}/files/{job['files'][0]['id']}", headers=world["one"])
    assert resp.status_code == 503


async def test_progress_extends_deadline_and_only_owner_may_report(client, world):
    await new_job()
    job = await claimed(client, world["one"])
    url = f"/api/runner/jobs/{job['id']}/progress"
    assert (await client.post(url, headers=world["two"], json={"step": "x"})).status_code == 404
    async with async_session_factory() as db, db.begin():
        await db.execute(update(Job).values(deadline_at=db_now_plus(5)))
    assert (await client.post(url, headers=world["one"], json={"step": "unduh"})).status_code == 200
    async with async_session_factory() as db:
        await service.expire_jobs(db)
        saved = await db.get(Job, job["id"])
    assert saved.step == "unduh" and saved.status == service.STATUS_RUNNING


async def test_expired_job_is_not_run_not_failed(client, world):
    await new_job()
    job = await claimed(client, world["one"])
    async with async_session_factory() as db, db.begin():
        await db.execute(update(Job).values(deadline_at=db_now_plus(-5)))
    async with async_session_factory() as db:
        await service.expire_jobs(db)
        saved = await db.get(Job, job["id"])
    assert saved.status == service.STATUS_NOT_RUN and saved.finished_at is not None
    late = await client.post(f"/api/runner/jobs/{job['id']}/result", headers=world["one"], json={"status": "Lolos"})
    assert late.status_code == 409


async def test_result_records_evidence_only_for_own_files(client, world):
    await new_job()
    job = await claimed(client, world["one"])
    url = f"/api/runner/jobs/{job['id']}/result"
    async with async_session_factory() as db:
        other_file = await db.scalar(select(Backup.id).where(Backup.relpath.like("20261029%")))
    refused = await client.post(url, headers=world["one"], json={"status": "Lolos", "results": [
        {"kind": "full", "backup_id": other_file, "status": "Lolos"}]})
    assert refused.status_code == 403
    file_id = job["files"][0]["id"]
    done = await client.post(url, headers=world["one"], json={"status": "Lolos", "results": [
        {"kind": "full", "backup_id": file_id, "status": "Lolos", "duration_seconds": 12}]})
    assert done.status_code == 200
    async with async_session_factory() as db:
        rows = list(await db.scalars(select(RestoreTest)))
        saved = await db.get(Job, job["id"])
    assert [(r.backup_id, r.status, r.runner) for r in rows] == [(file_id, "Lolos", "runner-1")]
    assert saved.status == "Lolos"


async def test_cannot_create_job_without_files(world):
    with pytest.raises(service.RunnerError) as caught:
        await new_job(full=False, databases=[])
    assert caught.value.status_code == 409
