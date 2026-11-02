"""Skenario upload di dokumen pengembangan, bagian Pengujian."""
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.backups.models import Backup, IntegrityEvent
from app.backups import naming, service as backup_service
from app.core.config import settings
from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.agents.dependencies import registry

DATA = Path(settings.DATA_DIR)


async def set_nas(state="ok"):
    async with async_session_factory() as db:
        await save_result(db, ProbeResult(state, 1000, 800))


@pytest.fixture
async def agent():
    token = registry.add("siak-db")
    await set_nas()
    return {"X-Token": token}


def checksum(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


async def rows(**filters):
    async with async_session_factory() as db:
        stmt = select(Backup).filter_by(**filters).order_by(Backup.id)
        return list(await db.scalars(stmt))


async def test_four_kinds_stored_with_expected_layout(client, agent):
    files = [
        ("fullbackup", "20261030_020000.xbstream.gz", b"full"),
        ("databases", "siakdb/siakdb-20261030_030000.sql.gz", b"dump"),
        ("databases", "_users/_users-20261030_030000.sql.gz", b"users"),
        ("binlog", "mysql-bin.000001.gz", b"binlog"),
    ]
    for kind, relpath, body in files:
        resp = await client.put(f"/api/upload/{kind}/{relpath}", content=body,
                                headers={**agent, "X-SHA256": checksum(body), "X-File-Mtime": "1700000000"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"] == "stored"
        assert (DATA / "siak-db" / kind / relpath).read_bytes() == body
    saved = await rows(container="siak-db")
    assert len(saved) == 4
    assert {row.database for row in saved} == {None, "siakdb", "_users"}


async def test_repeat_upload_answers_exists(client, agent):
    url = "/api/upload/fullbackup/20261030_020000.xbstream.gz"
    assert (await client.put(url, content=b"x", headers=agent)).json()["result"] == "stored"
    assert (await client.put(url, content=b"x", headers=agent)).json()["result"] == "exists"
    assert len(await rows()) == 1
    assert not list((DATA / "siak-db" / "fullbackup").glob("*.part"))


async def test_nas_not_mounted_rejects_and_writes_nothing(client, agent):
    await set_nas("down")
    resp = await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers=agent)
    assert resp.status_code == 503
    assert not (DATA / "siak-db").exists()


async def test_nas_hanging_rejects(client, agent):
    await set_nas("hang")
    assert (await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers=agent)).status_code == 503


async def test_stale_nas_status_rejects(client, agent):
    # Pengecek berhenti: mount tidak lagi diketahui, jadi tidak boleh menulis ke disk lokal
    from sqlalchemy import text
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE nas_status SET checked_at = NOW() - INTERVAL 1 HOUR"))
    assert (await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers=agent)).status_code == 503


async def test_wrong_token_401(client, agent):
    resp = await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers={"X-Token": "salah-salah-salah"})
    assert resp.status_code == 401


async def test_disabled_agent_rejected(client, agent):
    registry.disable("siak-db")
    assert (await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers=agent)).status_code == 401


@pytest.mark.parametrize("kind,relpath", [
    ("databases", "..%2F..%2Fetc%2Fpasswd.gz"),
    ("databases", "a/..%2F..%2Fb.gz"),
    ("fullbackup", "..%2Fa.gz"),
    ("fullbackup", "a/b.gz"),
    ("databases", "a.gz"),
    ("binlog", ".hidden.gz"),
    ("fullbackup", "a.part"),
    ("binlog", "bukan-binlog.gz"),
    ("rahasia", "a.gz"),
    ("databases", "d" * 65 + "/a.sql.gz"),
    ("binlog", "mysql-bin.9999999999.gz"),
])
async def test_invalid_paths_400(client, agent, kind, relpath):
    resp = await client.put(f"/api/upload/{kind}/{relpath}", content=b"x", headers=agent)
    assert resp.status_code == 400, resp.text
    assert not list(Path(settings.DATA_DIR).parent.glob("etc"))


async def test_oversized_request_header_is_ignored(client, agent):
    resp = await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers={**agent, "X-Backup-Request": "9" * 25})
    assert resp.status_code == 200
    assert (await rows())[0].request_id is None


async def test_wrong_checksum_422_leaves_no_part(client, agent):
    resp = await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers={**agent, "X-SHA256": "0" * 64})
    assert resp.status_code == 422
    leftovers = [p for p in (DATA / "siak-db").rglob("*") if p.is_file()]
    assert leftovers == []
    assert await rows() == []


async def test_empty_file_422(client, agent):
    assert (await client.put("/api/upload/fullbackup/a.gz", content=b"", headers=agent)).status_code == 422
    assert [p for p in (DATA / "siak-db").rglob("*") if p.is_file()] == []


async def test_same_name_different_content_kept_as_version(client, agent):
    url = "/api/upload/databases/siakdb/siakdb-20261029_010000.sql.gz"
    assert (await client.put(url, content=b"satu", headers=agent)).json()["result"] == "stored"
    resp = await client.put(url, content=b"dua", headers=agent)
    assert resp.json()["result"] == "version"
    base = DATA / "siak-db" / "databases" / "siakdb" / "siakdb-20261029_010000.sql.gz"
    assert base.read_bytes() == b"satu"            # file yang sudah ada tidak pernah ditimpa
    second = base.with_name(f"{base.name}.v2-{checksum(b'dua')[:8]}")
    assert second.read_bytes() == b"dua"
    assert resp.json()["stored_path"].endswith(second.name)
    # versi yang sama dikirim ulang: aman diulang
    assert (await client.put(url, content=b"dua", headers=agent)).json()["result"] == "exists"
    async with async_session_factory() as db:
        events = list(await db.scalars(select(IntegrityEvent)))
    assert len(events) == 1


async def test_existing_file_without_metadata_is_not_overwritten(client, agent):
    # Metadata hilang tetapi file di NAS masih ada: isi berbeda menjadi versi, isi sama dipakai ulang
    target = DATA / "siak-db" / "fullbackup" / "20261030_020000.xbstream.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"lama")
    url = "/api/upload/fullbackup/20261030_020000.xbstream.gz"
    await client.put(url, content=b"baru", headers=agent)
    assert target.read_bytes() == b"lama"
    assert (await rows())[0].stored_path.startswith("fullbackup/20261030_020000.xbstream.gz.v2-")
    (DATA / "siak-db" / "fullbackup" / "x.gz").write_bytes(b"sama")
    await client.put("/api/upload/fullbackup/x.gz", content=b"sama", headers=agent)
    assert (await rows())[1].stored_path == "fullbackup/x.gz"


async def test_local_archive_name_normalized(client, agent):
    url = "/api/upload/binlog/mysql-bin.000012.gz"
    await client.put(url, content=b"isi", headers={**agent, "X-File-Mtime": "1700000000"})
    resp = await client.put("/api/upload/binlog/mysql-bin.000012.20261101_120000.gz", content=b"isi", headers=agent)
    assert resp.json()["result"] == "exists"


def binlog_headers(agent, mtime):
    return {**agent, "X-File-Mtime": str(mtime)}


async def test_chain_restarts_when_number_goes_back(client, agent):
    # Satu arsip nomor sama setelah restore: rantai baru, bukan temuan integritas
    base = 1_700_000_000
    for offset, number, body in ((0, 5, b"a"), (10, 6, b"b"), (20, 1, b"c"), (30, 5, b"d")):
        resp = await client.put(f"/api/upload/binlog/mysql-bin.{number:06}.gz", content=body,
                                headers=binlog_headers(agent, base + offset))
        assert resp.status_code == 200
    saved = await rows(kind="binlog")
    assert [(r.binlog_no, r.chain_id) for r in sorted(saved, key=lambda r: r.backup_at)] == [
        (5, 1), (6, 1), (1, 2), (5, 2),
    ]
    async with async_session_factory() as db:
        assert list(await db.scalars(select(IntegrityEvent))) == []
    versioned = await rows(binlog_no=5)
    assert versioned[1].stored_path.startswith("binlog/mysql-bin.000005.gz.v2-")


async def test_late_binlog_stays_in_old_chain(client, agent):
    base = 1_700_000_000
    await client.put("/api/upload/binlog/mysql-bin.000005.gz", content=b"a", headers=binlog_headers(agent, base))
    await client.put("/api/upload/binlog/mysql-bin.000001.gz", content=b"b", headers=binlog_headers(agent, base + 100))
    # nomor 6 baru terkirim belakangan, tetapi waktu ubahnya tetap waktu lama
    await client.put("/api/upload/binlog/mysql-bin.000006.gz", content=b"c", headers=binlog_headers(agent, base + 50))
    assert {r.binlog_no: r.chain_id for r in await rows(kind="binlog")} == {5: 1, 6: 1, 1: 2}


async def test_full_records_binlog_start_and_chain(client, agent):
    stamp = (datetime(2023, 11, 14, 22, 0, tzinfo=UTC) + timedelta(hours=7)).strftime("%Y%m%d_%H%M%S")
    await client.put(f"/api/upload/fullbackup/{stamp}.xbstream.gz", content=b"f",
                     headers={**agent, "X-Binlog-Start": "mysql-bin.000007"})
    full = (await rows(kind="fullbackup"))[0]
    assert full.binlog_start == "mysql-bin.000007" and full.chain_id is None
    after = int(datetime(2023, 11, 14, 23, 0, tzinfo=UTC).timestamp())
    await client.put("/api/upload/binlog/mysql-bin.000007.gz", content=b"b", headers=binlog_headers(agent, after))
    assert (await rows(kind="fullbackup"))[0].chain_id == 1


async def test_invalid_binlog_start_ignored(client, agent):
    await client.put("/api/upload/fullbackup/a.gz", content=b"f", headers={**agent, "X-Binlog-Start": "x;rm -rf"})
    assert (await rows())[0].binlog_start is None


async def test_upload_limit_returns_503(client, agent, monkeypatch):
    import app.backups.router as upload_router
    monkeypatch.setattr(upload_router, "_active_uploads", backup_service.PROCESS_SLOTS)
    assert (await client.put("/api/upload/fullbackup/a.gz", content=b"x", headers=agent)).status_code == 503


async def test_backup_at_sets_file_mtime(client, agent):
    await client.put("/api/upload/binlog/mysql-bin.000001.gz", content=b"x", headers=binlog_headers(agent, 1_700_000_000))
    stat = os.stat(DATA / "siak-db" / "binlog" / "mysql-bin.000001.gz")
    assert int(stat.st_mtime) == 1_700_000_000
    assert (await rows())[0].backup_at == datetime(2023, 11, 14, 22, 13, 20)


def test_backup_time_prefers_name_in_wib():
    now = datetime(2026, 11, 2, 0, 0)
    path = naming.parse_upload_path("fullbackup", "20261101_070000.xbstream.gz")
    assert naming.backup_time(path, "1700000000", now) == datetime(2026, 11, 1, 0, 0)


def test_backup_time_rejects_far_future():
    now = datetime(2026, 11, 2, 0, 0)
    path = naming.parse_upload_path("fullbackup", "20270101_070000.xbstream.gz")
    future = int(datetime(2030, 1, 1, tzinfo=UTC).timestamp())
    assert naming.backup_time(path, str(future), now) == now


async def test_stuck_nas_write_does_not_block_dashboard_or_alerts(client, agent, monkeypatch):
    """NFS macet saat menulis: upload itu tersangkut, kelebihannya 503, tetapi aplikasi tetap menjawab."""
    import threading

    import anyio

    from app.backups import storage

    release = threading.Event()
    real_write = storage.write_chunk

    def stuck_write(part, chunk):
        release.wait(20)
        real_write(part, chunk)

    monkeypatch.setattr(storage, "write_chunk", stuck_write)
    results = []

    async def uploader(name):
        results.append(await client.put(f"/api/upload/fullbackup/{name}.gz", content=b"x", headers=agent))

    async with anyio.create_task_group() as group:
        for index in range(backup_service.PROCESS_SLOTS):
            group.start_soon(uploader, f"macet{index}")
        with anyio.fail_after(5):
            import app.backups.router as upload_router
            while upload_router._active_uploads < backup_service.PROCESS_SLOTS:
                await anyio.sleep(0.01)
            over = await client.put("/api/upload/fullbackup/lain.gz", content=b"x", headers=agent)
            assert over.status_code == 503
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/api/watchdog")).status_code == 200
        release.set()
    assert [r.status_code for r in results] == [200] * backup_service.PROCESS_SLOTS
