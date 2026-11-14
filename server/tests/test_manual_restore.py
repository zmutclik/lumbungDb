"""Restore manual di server: sesi, token, ikatan ke satu mesin, unduhan, pendaftaran, dan perlindungan retensi."""
import hashlib

import pytest
from sqlalchemy import select, update

from app.agents.dependencies import registry as agents
from app.backup_control.models import BackupRequest, ControlLog
from app.backups.models import Backup
from app.containers.models import Container
from app.core.database import async_session_factory
from app.manual_restore import service
from app.manual_restore.models import ManualRestore, ManualRestoreDownload, ManualRestoreFile
from app.runners import service as runner_service
from app.runners.models import ContainerSettings
from app.runners.schemas import RunnerHeartbeatIn
from tests.test_runners import BODY, clean_files, set_nas, world  # noqa: F401
from tests.test_standby import add_backup

NAME = "siak-db"
MACHINE = "d" * 32
OTHER = "e" * 32


async def chain_for_full():
    """Full dari world mendapat rantai 1 dan tiga binlog (1 sampai 3) di rantai yang sama."""
    async with async_session_factory() as db, db.begin():
        await db.execute(update(Backup).where(Backup.kind == "fullbackup", Backup.relpath.like("20261030%"))
                         .values(chain_id=1, binlog_start="mysql-bin.000001"))
    for no in (1, 2, 3):
        await add_backup("binlog", f"b{no}", 1, no=no, chain=1)
    await add_backup("binlog", "lain", 1, no=9, chain=2)             # rantai lain tidak ikut


async def newest_full_id():
    async with async_session_factory() as db:
        return await db.scalar(select(Backup.id).where(Backup.relpath.like("20261030%")))


async def make(binlog="all", new="siak-db-2", port=3310, hours=4, full_id=None):
    async with async_session_factory() as db:
        return await service.create(db, NAME, full_id or await newest_full_id(), binlog, new, port, hours, "alice")


def headers(token, machine=MACHINE):
    return {"X-Restore-Token": token, "X-Machine-Id": machine}


@pytest.fixture(autouse=True)
def reset_failures():
    service._failures.clear()
    yield
    service._failures.clear()


async def file_ids(restore_id):
    async with async_session_factory() as db:
        return set(await db.scalars(select(ManualRestoreFile.backup_id).where(ManualRestoreFile.restore_id == restore_id)))


async def test_session_has_full_and_binlogs_of_its_chain(client, world):  # noqa: F811
    await chain_for_full()
    row, token = await make()
    assert token.startswith("lmr_") and row.status == "Aktif"
    async with async_session_factory() as db:
        kinds = [(await db.get(Backup, i)).relpath for i in await file_ids(row.id)]
        assert sorted(kinds) == sorted(["20261030_020000.xbstream.gz.age", "b1", "b2", "b3"][:1] + ["b1", "b2", "b3"])
        stored = await db.get(ManualRestore, row.id)
        assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest() and token not in str(stored.__dict__)
        log = (await db.scalars(select(ControlLog).where(ControlLog.action == "Buat sesi Restore manual"))).one()
    assert "siak-db-2" in log.detail


async def test_full_only_session_has_no_binlog(client, world):  # noqa: F811
    await chain_for_full()
    row, _ = await make(binlog="none")
    assert len(await file_ids(row.id)) == 1


@pytest.mark.parametrize("change,message", [
    ({"port": 80}, "Port"), ({"hours": 3}, "Batas waktu"), ({"binlog": "x"}, "binlog"),
    ({"new": NAME}, "sudah terdaftar"), ({"new": "bad name"}, "Hanya huruf"),
])
async def test_invalid_input_is_refused(client, world, change, message):  # noqa: F811
    with pytest.raises(service.RestoreError) as err:
        await make(**change)
    assert message in err.value.detail and err.value.status_code == 422


async def test_full_of_another_container_or_wrong_kind_is_refused(client, world):  # noqa: F811
    await chain_for_full()
    async with async_session_factory() as db, db.begin():
        db.add(Container(name="lain"))
    other = await add_backup("fullbackup", "x", 1)
    async with async_session_factory() as db, db.begin():
        await db.execute(update(Backup).where(Backup.id == other).values(container="lain"))
    for bad in (other, await add_backup("binlog", "bb", 1, no=7, chain=1), 999999):
        with pytest.raises(service.RestoreError) as err:
            await make(full_id=bad)
        assert err.value.status_code == 422


async def test_name_is_reserved_while_session_is_live_and_freed_when_revoked(client, world):  # noqa: F811
    row, _ = await make()
    with pytest.raises(service.RestoreError) as err:
        await make(new="SIAK-DB-2")
    assert "sudah terdaftar atau dicadangkan" in err.value.detail
    async with async_session_factory() as db:
        await service.revoke(db, row.id, "alice")
    await make()          # sesuai: nama kembali bebas


async def test_nas_problem_and_unwritable_agents_file_refuse_creation(client, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agents, "writable", lambda: False)
    with pytest.raises(service.RestoreError) as err:
        await make()
    assert err.value.status_code == 500 and "agents.conf" in err.value.detail
    monkeypatch.undo()
    await set_nas("down")
    with pytest.raises(service.RestoreError) as err:
        await make()
    assert err.value.status_code == 503


async def claim(client, token, machine=MACHINE):
    return await client.post("/api/manual/session", headers=headers(token, machine))


async def test_claim_binds_machine_and_lists_files(client, world):  # noqa: F811
    await chain_for_full()
    row, token = await make()
    reply = await claim(client, token)
    assert reply.status_code == 200
    values = dict(line.split("=", 1) for line in reply.text.splitlines() if "=" in line)
    assert (values["NAME"], values["PORT"], values["SOURCE"], values["BINLOG_START"]) == ("siak-db-2", "3310", NAME, "mysql-bin.000001")
    assert values["IMAGE_TAG"] == "12.3-a2"
    files = [line.split() for line in reply.text.splitlines() if line.startswith("FILE ")]
    assert [f[2] for f in files] == ["fullbackup", "binlog", "binlog", "binlog"] and [f[5] for f in files][1:] == ["1", "2", "3"]
    async with async_session_factory() as db:
        saved = await db.get(ManualRestore, row.id)
    assert (saved.status, saved.machine_id) == ("Berjalan", MACHINE)
    assert (await claim(client, token)).status_code == 200            # mesin yang sama boleh mengulang
    other = await claim(client, token, OTHER)
    assert other.status_code == 403 and "mesin lain" in other.text


async def test_claim_uses_image_tag_reported_by_source_agent(client, world):  # noqa: F811
    await client.post("/api/heartbeat", headers=world["agent"], json={"server": "db1", "agent_version": "11.4-a2"})
    _, token = await make()
    assert "IMAGE_TAG=11.4-a2" in (await claim(client, token)).text


@pytest.mark.parametrize("token", [None, "salah", "lmr_" + "z" * 40])
async def test_unknown_token_is_401_and_machine_id_is_required(client, world, token):  # noqa: F811
    assert (await client.post("/api/manual/session", headers={"X-Restore-Token": token or "", "X-Machine-Id": MACHINE})).status_code == 401
    _, real = await make()
    assert (await client.post("/api/manual/session", headers={"X-Restore-Token": real})).status_code == 422


async def test_wrong_tokens_are_rate_limited_per_ip(client, world):  # noqa: F811
    for _ in range(service._FAIL_LIMIT):
        assert (await claim(client, "lmr_salah")).status_code == 401
    _, token = await make()
    blocked = await claim(client, token)
    assert blocked.status_code == 429 and blocked.headers["retry-after"]


async def test_expired_revoked_and_finished_sessions_are_refused(client, world):  # noqa: F811
    row, token = await make()
    async with async_session_factory() as db, db.begin():
        await db.execute(update(ManualRestore).where(ManualRestore.id == row.id)
                         .values(expires_at=service.db_time_plus(service.func.now(), -60)))
    expired = await claim(client, token)
    assert expired.status_code == 401 and "kedaluwarsa" in expired.text
    other, other_token = await make(new="siak-db-3")
    async with async_session_factory() as db:
        await service.revoke(db, other.id, "alice")
    assert "dicabut" in (await claim(client, other_token)).text
    sessions = await service_sessions()
    assert {s["status"] for s in sessions} == {"Kedaluwarsa", "Dicabut"}


async def service_sessions():
    async with async_session_factory() as db:
        return await service.sessions_for(db, NAME)


async def test_extend_stops_at_maximum_and_revoke_ends_it(client, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(service.settings, "MANUAL_RESTORE_MAX_HOURS", 4)
    row, token = await make(hours=2)

    async def call(action, *args):
        async with async_session_factory() as db:
            return await action(db, row.id, "alice", *args)

    await call(service.extend)
    with pytest.raises(service.RestoreError) as err:
        await call(service.extend)
    assert "batas 4 jam" in err.value.detail
    async with async_session_factory() as db:
        saved = await db.get(ManualRestore, row.id)
        assert saved.expires_at == saved.max_expires_at
    await call(service.revoke)
    for action in (service.extend, service.revoke):
        with pytest.raises(service.RestoreError):
            await call(action)
    assert "dicabut" in (await claim(client, token)).text


async def test_download_only_own_files_resume_and_logged(client, world):  # noqa: F811
    row, token = await make(binlog="none")
    full_id = await newest_full_id()
    whole = await client.get(f"/api/manual/files/{full_id}", headers=headers(token))
    assert whole.status_code == 200 and whole.content == BODY
    assert whole.headers["x-sha256"] == hashlib.sha256(BODY).hexdigest()
    part = await client.get(f"/api/manual/files/{full_id}", headers={**headers(token), "Range": "bytes=50-"})
    assert part.status_code == 206 and part.content == BODY[50:]
    async with async_session_factory() as db:
        older = await db.scalar(select(Backup.id).where(Backup.relpath.like("20261029%")))
        assert (await db.scalars(select(ManualRestoreDownload.offset).order_by(ManualRestoreDownload.id))).all() == [0, 50]
    assert (await client.get(f"/api/manual/files/{older}", headers=headers(token))).status_code == 403
    assert (await client.get(f"/api/manual/files/{full_id}", headers=headers(token, OTHER))).status_code in (200, 403)
    await claim(client, token)           # mengikat ke MACHINE
    assert (await client.get(f"/api/manual/files/{full_id}", headers=headers(token, OTHER))).status_code == 403
    assert (await client.get(f"/api/manual/files/{full_id}", headers={**headers(token), "Range": "bytes=0-5"})).status_code == 416
    assert row.id


async def test_download_waits_while_nas_has_problem(client, world):  # noqa: F811
    _, token = await make(binlog="none")
    await set_nas("down")
    assert (await client.get(f"/api/manual/files/{await newest_full_id()}", headers=headers(token))).status_code == 503


async def progress(client, token, body):
    return await client.post("/api/manual/progress", headers=headers(token), content=body)


async def test_progress_steps_failure_and_rerun(client, world):  # noqa: F811
    row, token = await make()
    await claim(client, token)
    assert (await progress(client, token, "ok mengunduh file 1")).status_code == 200
    async with async_session_factory() as db:
        assert (await db.get(ManualRestore, row.id)).step == "mengunduh file 1"
    assert (await progress(client, token, "fail Docker tidak bisa dipakai")).status_code == 200
    async with async_session_factory() as db:
        saved = await db.get(ManualRestore, row.id)
    assert (saved.status, saved.note) == ("Gagal", "Docker tidak bisa dipakai")
    assert (await progress(client, token, "huh")).status_code == 422
    assert (await claim(client, token)).status_code == 200          # perintah yang sama dijalankan ulang
    async with async_session_factory() as db:
        assert (await db.get(ManualRestore, row.id)).status == "Berjalan"


async def register(client, token, machine=MACHINE):
    return await client.post("/api/manual/register", headers=headers(token, machine))


async def beat(runner, machine):
    async with async_session_factory() as db:
        await runner_service.record_heartbeat(db, runner, RunnerHeartbeatIn(server=runner, machine_id=machine))


async def test_register_creates_container_with_runner_on_another_server(client, world):  # noqa: F811
    async with async_session_factory() as db, db.begin():
        db.add(ContainerSettings(container=NAME, test_runner="runner-1", test_schedule="30 3 * * 0", standby_enabled=True,
                                 standby_full_schedule="0 2 * * *"))
    await beat("runner-1", MACHINE)           # satu mesin dengan server tujuan
    await beat("runner-2", OTHER)
    row, token = await make()
    assert (await register(client, token)).status_code == 409           # belum menjalankan script
    await claim(client, token)
    reply = await register(client, token)
    assert reply.status_code == 200
    values = dict(line.split("=", 1) for line in reply.text.splitlines())
    assert values["LUMBUNG_TOKEN"] == agents.entries().active["siak-db-2"] and values["LUMBUNG_AGE_RECIPIENTS"].startswith("age1")
    async with async_session_factory() as db:
        container = await db.get(Container, "siak-db-2")
        cfg = await db.get(ContainerSettings, "siak-db-2")
        first_full, = (await db.scalars(select(BackupRequest).where(BackupRequest.container == "siak-db-2"))).all()
        saved = await db.get(ManualRestore, row.id)
    assert (container.origin, container.source_container) == ("manual", NAME)
    assert (cfg.test_runner, cfg.standby_runner, cfg.standby_enabled) == ("runner-2", "runner-2", True)
    assert (first_full.kind, first_full.status) == ("full", "Menunggu") and saved.status == "Selesai"
    again = await register(client, token)
    assert again.status_code == 401 and "selesai" in again.text           # token mati sesudah mendaftar


async def test_register_uses_same_server_runner_when_no_other(client, world):  # noqa: F811
    await beat("runner-1", MACHINE)
    await beat("runner-2", MACHINE)           # kedua runner satu mesin dengan server tujuan
    _, token = await make()
    await claim(client, token)
    assert (await register(client, token)).status_code == 200
    async with async_session_factory() as db:
        assert (await db.get(ContainerSettings, "siak-db-2")).test_runner == "runner-1"


async def test_register_failure_to_write_agents_file_keeps_session_for_rerun(client, world, monkeypatch):  # noqa: F811
    from app.agents.agents_file import AgentsFileWriteError
    row, token = await make()
    await claim(client, token)

    def boom(*args, **kwargs):
        raise AgentsFileWriteError("ro")
    monkeypatch.setattr(agents, "add", boom)
    failed = await register(client, token)
    assert failed.status_code == 500 and "tidak dipulihkan ulang" in failed.text
    monkeypatch.undo()
    assert "siak-db-2" not in agents.entries().all_names
    async with async_session_factory() as db:
        assert (await db.get(ManualRestore, row.id)).status == "Berjalan" and await db.get(Container, "siak-db-2") is None
    assert (await register(client, token)).status_code == 200


async def test_register_from_other_machine_is_forbidden(client, world):  # noqa: F811
    _, token = await make()
    await claim(client, token)
    assert (await register(client, token, OTHER)).status_code == 403
    assert "siak-db-2" not in agents.entries().all_names


async def test_live_session_files_are_protected_from_retention(client, world):  # noqa: F811
    await chain_for_full()
    row, _ = await make()
    async with async_session_factory() as db, db.begin():
        assert await service.protected_backup_ids(db) == await file_ids(row.id)
    async with async_session_factory() as db:
        await service.revoke(db, row.id, "alice")
    async with async_session_factory() as db, db.begin():
        assert await service.protected_backup_ids(db) == set()


async def test_install_script_endpoint_serves_script_without_login(client):
    reply = await client.get("/restore.sh")
    assert reply.status_code == 200 and reply.text.startswith("#!/bin/sh") and "AGE_IDENTITY_FILE" in reply.text
    assert "lmr_" in reply.text and "RESTORE_TOKEN" in reply.text


