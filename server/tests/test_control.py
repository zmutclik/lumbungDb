"""Heartbeat, jadwal, dan permintaan backup, lewat API asli."""
import pytest
from sqlalchemy import text

from app.backup_control import service as control
from app.backup_control.models import BackupRequest, BackupSchedule, ControlLog
from app.backup_control.schemas import HeartbeatIn
from app.containers.models import Heartbeat
from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.agents.dependencies import registry
from tests.conftest import login, make_user

SCHEDULE = {"version": 0, "source": "bawaan", "full": "0 1 * * *", "dump": "0 3 * * *", "binlog": "*/15 * * * *"}


def payload(**overrides):
    data = {
        "server": "db-utama", "machine_id": "a" * 32, "agent_version": "12.3-a1", "mariadb_version": "12.3.1",
        "disk": {"total": 1000, "free": 900}, "files": {"fullbackup": 0}, "log": ["2026-11-02 baris"],
        "pending": {"count": 0, "bytes": 0}, "schedule": dict(SCHEDULE),
    }
    data.update(overrides)
    return data


@pytest.fixture
async def agent():
    token = registry.add("siak-db")
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))
    return {"X-Token": token}


async def beat(client, agent, **overrides):
    resp = await client.post("/api/heartbeat", json=payload(**overrides), headers=agent)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def get_row(model, key):
    async with async_session_factory() as db:
        return await db.get(model, key)


async def set_schedule(values, actor="admin", name="siak-db"):
    async with async_session_factory() as db:
        return await control.update_schedule(db, name, values, actor)


async def new_request(kind="dump", database=None, name="siak-db"):
    async with async_session_factory() as db:
        return await control.create_request(db, name, kind, database, "admin")


async def test_heartbeat_requires_token(client):
    assert (await client.post("/api/heartbeat", json=payload(), headers={"X-Token": "x" * 20})).status_code == 401


async def test_heartbeat_stores_identity_and_adopts_reported_schedule(client, agent):
    reply = await beat(client, agent, schedule={**SCHEDULE, "version": 3, "source": "lumbung"})
    assert "schedule" not in reply and reply["requests"] == []
    row = await get_row(Heartbeat, "siak-db")
    assert row.server == "db-utama" and row.info["mariadb_version"] == "12.3.1"
    # Metadata hilang: jadwal di agen tidak ditimpa, laporannya menjadi nilai awal
    schedule = await get_row(BackupSchedule, "siak-db")
    assert (schedule.requested_version, schedule.applied_version) == (3, 3)
    assert schedule.requested_full == SCHEDULE["full"]


async def test_schedule_flow_until_applied(client, agent):
    await beat(client, agent)
    values = {"full": "30 2 * * *", "dump": "0 4 * * *", "binlog": "*/30 * * * *"}
    assert await set_schedule(values) is True
    assert await set_schedule(values) is False        # tanpa perubahan: versi tidak naik
    reply = await beat(client, agent)
    assert reply["schedule"] == {"version": 1, **values}
    # Agen melaporkan versi lama: tetap dikirim. Dashboard tetap menampilkan jadwal yang dilaporkan agen.
    assert "schedule" in await beat(client, agent)
    assert (await get_row(BackupSchedule, "siak-db")).effective_full == SCHEDULE["full"]
    reply = await beat(client, agent, schedule={**values, "version": 1, "source": "lumbung"})
    assert "schedule" not in reply
    row = await get_row(BackupSchedule, "siak-db")
    assert (row.applied_version, row.effective_full, row.source) == (1, "30 2 * * *", "lumbung")
    async with async_session_factory() as db:
        logs = (await db.execute(text("SELECT detail FROM control_log"))).scalars().all()
    assert len(logs) == 1 and "Tiap hari 01:00 → Tiap hari 02:30" in logs[0]


async def test_rejected_schedule_reported_and_not_resent(client, agent):
    await beat(client, agent)
    await set_schedule({"full": "30 2 * * *", "dump": "0 4 * * *", "binlog": "*/30 * * * *"})
    assert "schedule" in await beat(client, agent)
    reply = await beat(client, agent, schedule={**SCHEDULE, "error": {"version": 1, "message": "crontab menolak"}})
    assert "schedule" not in reply
    row = await get_row(BackupSchedule, "siak-db")
    assert row.error_message == "crontab menolak" and row.applied_version == 0
    assert "schedule" not in await beat(client, agent, schedule={**SCHEDULE, "error": {"version": 1, "message": "x"}})
    # Admin mengubah lagi: versi naik, error hilang, dan dikirim lagi
    await set_schedule({"full": "40 2 * * *", "dump": "0 4 * * *", "binlog": "*/30 * * * *"})
    assert (await beat(client, agent))["schedule"]["version"] == 2
    assert (await get_row(BackupSchedule, "siak-db")).error_message is None


@pytest.mark.parametrize("bad", ["rm -rf /", "0 2 * *", "$(id) * * * *", "61 * * * *",
                                 ",".join(["1"] * 100) + " 2 * * *"])
async def test_invalid_schedule_refused(client, agent, bad):
    await beat(client, agent)
    with pytest.raises(control.ControlError):
        await set_schedule({"full": bad, "dump": "off", "binlog": "off"})
    assert (await get_row(BackupSchedule, "siak-db")).requested_version == 0


async def test_request_lifecycle(client, agent):
    await beat(client, agent)
    created = await new_request("dump", "siakdb")
    reply = await beat(client, agent)
    assert reply["requests"] == [{"id": created.id, "kind": "dump", "database": "siakdb"}]
    url = f"/api/agent/requests/{created.id}/status"
    assert (await client.post(url, json={"status": "Berjalan"}, headers=agent)).status_code == 200
    assert (await beat(client, agent))["requests"] == []     # hanya diambil sekali
    assert (await client.post(url, json={"status": "Mengirim"}, headers=agent)).status_code == 200
    resp = await client.post(url, json={"status": "Selesai", "note": "2 file terkirim"}, headers=agent)
    assert resp.status_code == 200
    row = await get_row(BackupRequest, created.id)
    assert row.status == "Selesai" and row.finished_at is not None and row.note == "2 file terkirim"
    # Selesai tidak bisa dimundurkan
    assert (await client.post(url, json={"status": "Berjalan"}, headers=agent)).status_code == 409


async def test_token_cannot_touch_other_container_request(client, agent):
    other = registry.add("lain-db")
    await beat(client, agent)
    created = await new_request("full")
    resp = await client.post(f"/api/agent/requests/{created.id}/status", json={"status": "Berjalan"},
                             headers={"X-Token": other})
    assert resp.status_code == 404


async def test_one_active_request_per_kind(client, agent):
    await beat(client, agent)
    await new_request("full")
    with pytest.raises(control.ControlError, match="aktif"):
        await new_request("full")
    await new_request("binlog")      # jenis lain boleh


async def test_cancel_only_before_picked_up(client, agent):
    await beat(client, agent)
    created = await new_request("full")
    async with async_session_factory() as db:
        assert await control.cancel_request(db, "siak-db", created.id, "admin") is True
    assert (await get_row(BackupRequest, created.id)).status == "Dibatalkan"
    # Dibatalkan: agen tidak bisa mengambilnya
    url = f"/api/agent/requests/{created.id}/status"
    assert (await client.post(url, json={"status": "Berjalan"}, headers=agent)).status_code == 409
    picked = await new_request("full")
    await client.post(f"/api/agent/requests/{picked.id}/status", json={"status": "Berjalan"}, headers=agent)
    async with async_session_factory() as db:
        with pytest.raises(control.ControlError, match="diambil"):
            await control.cancel_request(db, "siak-db", picked.id, "admin")


async def test_request_refused_while_nas_down(client, agent):
    await beat(client, agent)
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("down"))
    with pytest.raises(control.ControlError) as caught:
        await new_request("full")
    assert caught.value.status_code == 503


async def test_full_rejected_when_disk_too_small(client, agent):
    await beat(client, agent, disk={"total": 1000, "free": 10})
    body = b"x" * 100
    await client.put("/api/upload/fullbackup/a.gz", content=body, headers=agent)
    created = await new_request("full")
    assert created.status == "Ditolak"
    assert (await beat(client, agent, disk={"total": 1000, "free": 10}))["requests"] == []


async def test_expired_request_never_runs(client, agent):
    await beat(client, agent)
    created = await new_request("full")
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE backup_requests SET created_at = NOW() - INTERVAL 16 MINUTE"))
    # Sebelum pengecek menandainya pun, heartbeat tidak menawarkannya
    assert (await beat(client, agent))["requests"] == []
    async with async_session_factory() as db:
        await control.expire_requests(db)
    assert (await get_row(BackupRequest, created.id)).status == "Kedaluwarsa"


async def test_running_request_ends_unfinished_when_heartbeat_stops(client, agent):
    await beat(client, agent)
    created = await new_request("full")
    url = f"/api/agent/requests/{created.id}/status"
    await client.post(url, json={"status": "Berjalan"}, headers=agent)
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE heartbeats SET at = NOW() - INTERVAL 11 MINUTE"))
    async with async_session_factory() as db:
        await control.expire_requests(db)
    assert (await get_row(BackupRequest, created.id)).status == "Tidak selesai"
    # Hanya jaringan yang putus: script ternyata selesai, agen boleh mengoreksi
    assert (await client.post(url, json={"status": "Selesai"}, headers=agent)).status_code == 200


async def test_heartbeat_schema_rejects_oversized_log():
    with pytest.raises(ValueError):
        HeartbeatIn.model_validate(payload(log=["x"] * 21))


async def test_control_log_records_actor(client, agent):
    await make_user()
    await beat(client, agent)
    csrf = await login(client)
    resp = await client.post("/containers/siak-db/requests", data={"what": "full"},
                             headers={"X-CSRF-Token": csrf, "HX-Request": "true"})
    assert resp.status_code == 200
    async with async_session_factory() as db:
        row = (await db.execute(text(f"SELECT actor, action FROM {ControlLog.__tablename__}"))).one()
    assert tuple(row) == ("alice", "Minta backup")
