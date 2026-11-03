"""Pindai ulang NAS: metadata dibangun lagi dari file, tanpa mengubah file dan tanpa menduplikasi."""
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.agents.dependencies import registry
from app.backups.models import Backup
from app.core.config import settings
from app.core.database import async_session_factory
from app.nas import scan
from app.nas.models import NasScan
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from tests.conftest import AGE
from tests.test_dashboard import csrf, post_headers  # noqa: F401

DATA = Path(settings.DATA_DIR)
NAME = "siak-db"


def put(relative, body=b"isi"):
    path = DATA / NAME / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(AGE + body)
    return path


@pytest.fixture
async def nas():
    registry.add(NAME)
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))


async def request(actor="alice"):
    async with async_session_factory() as db:
        return await scan.request_scan(db, actor)


async def run():
    async with async_session_factory() as db:
        return await scan.run_pending(db)


async def backups():
    async with async_session_factory() as db:
        return list(await db.scalars(select(Backup).order_by(Backup.stored_path)))


async def last_scan():
    async with async_session_factory() as db:
        return (await db.scalars(select(NasScan).order_by(NasScan.id.desc()))).first()


def populate():
    put("fullbackup/20261030_020000.xbstream.gz.age")
    put("databases/siak/siak-20261030_030000.sql.gz.age")
    put("databases/_users/_users-20261030_030000.sql.gz.age")
    put("binlog/mysql-bin.000011.gz.age")
    put("binlog/mysql-bin.000012.gz.age")
    put("binlog/mysql-bin.000002.gz.age")          # nomor mengecil: rantai baru


async def test_rescan_rebuilds_metadata_from_files(nas):
    populate()
    mtimes = {"mysql-bin.000011.gz.age": 1_788_000_000, "mysql-bin.000012.gz.age": 1_788_000_100,
              "mysql-bin.000002.gz.age": 1_788_000_200}
    for name, mtime in mtimes.items():
        os.utime(DATA / NAME / "binlog" / name, (mtime, mtime))
    await request()
    await run()
    rows = {b.stored_path: b for b in await backups()}
    assert len(rows) == 6
    full = rows["fullbackup/20261030_020000.xbstream.gz.age"]
    assert (full.kind, full.size, full.backup_at.isoformat()) == ("fullbackup", len(AGE) + 3, "2026-10-29T19:00:00")
    dump = rows["databases/siak/siak-20261030_030000.sql.gz.age"]
    assert (dump.kind, dump.database) == ("databases", "siak")
    assert rows["databases/_users/_users-20261030_030000.sql.gz.age"].database == "_users"
    assert [(rows[f"binlog/mysql-bin.{n:06d}.gz.age"].binlog_no, rows[f"binlog/mysql-bin.{n:06d}.gz.age"].chain_id)
            for n in (11, 12, 2)] == [(11, 1), (12, 1), (2, 2)]
    assert len(full.sha256) == 64 and full.plain_sha256 == full.sha256         # checksum isi tidak diketahui
    result = await last_scan()
    assert (result.status, result.seen, result.added, result.skipped) == ("Selesai", 6, 6, 0)


async def test_rescan_is_idempotent_and_leaves_files_alone(nas):
    path = put("fullbackup/20261030_020000.xbstream.gz.age", b"abc")
    before = (path.stat().st_mtime_ns, path.read_bytes())
    await request()
    await run()
    await request()
    await run()
    assert len(await backups()) == 1
    result = await last_scan()
    assert (result.seen, result.added) == (1, 0)
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


async def test_rescan_skips_unknown_names_foreign_containers_and_partial_files(nas):
    put("fullbackup/20261030_020000.xbstream.gz.age")
    put("fullbackup/.sedang-ditulis.part")
    put("fullbackup/catatan.txt")
    put("databases/siak/siak-20261030_030000.sql.gz.age")
    foreign = DATA / "bukan-container"
    (foreign / "fullbackup").mkdir(parents=True)
    (foreign / "fullbackup" / "x.gz.age").write_bytes(AGE)
    (DATA / ".lumbung-mounted").write_text("")
    link = DATA / NAME / "binlog"
    link.mkdir(parents=True, exist_ok=True)
    (link / "mysql-bin.000099.gz.age").symlink_to(DATA / NAME / "fullbackup" / "20261030_020000.xbstream.gz.age")
    await request()
    await run()
    names = [b.stored_path for b in await backups()]
    assert names == ["databases/siak/siak-20261030_030000.sql.gz.age", "fullbackup/20261030_020000.xbstream.gz.age"]
    result = await last_scan()
    assert result.added == 2 and result.skipped == 1       # container di luar daftar; sisanya tidak dikenali pemindai


async def test_versions_become_separate_rows(nas):
    put("fullbackup/20261030_020000.xbstream.gz.age", b"satu")
    put("fullbackup/20261030_020000.xbstream.gz.age.v2-ab12cd34", b"dua")
    await request()
    await run()
    rows = await backups()
    assert len(rows) == 2 and {b.relpath for b in rows} == {"20261030_020000.xbstream.gz.age"}
    assert len({b.plain_sha256 for b in rows}) == 2


async def test_only_one_pending_scan_and_waits_while_nas_has_a_problem(nas):
    assert await request() is not None
    assert await request() is None
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("down"))
    assert await run() is None
    assert (await last_scan()).status == "Menunggu"


async def test_stalled_scanner_fails_the_scan(nas, monkeypatch):
    put("fullbackup/20261030_020000.xbstream.gz.age")
    monkeypatch.setattr(scan, "SCANNER", Path(__file__).parent / "_stall.py")
    (Path(__file__).parent / "_stall.py").write_text("import time\ntime.sleep(30)\n")
    monkeypatch.setattr(scan.settings, "NAS_SCAN_STALL_SECONDS", 1)
    try:
        await request()
        start = time.monotonic()
        await run()
        assert time.monotonic() - start < 10
    finally:
        (Path(__file__).parent / "_stall.py").unlink(missing_ok=True)
    result = await last_scan()
    assert result.status == "Gagal" and "tidak merespons" in result.note


async def test_interrupted_scan_is_marked_failed_on_worker_start(nas):
    await request()
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE nas_scans SET status='Berjalan'"))
    await scan.reset_interrupted(async_session_factory)
    assert (await last_scan()).status == "Gagal"
    assert await request() is not None


async def test_settings_button_requests_scan(client, csrf, nas):  # noqa: F811
    page = await client.get("/settings")
    assert "Pindai ulang NAS" in page.text and "Belum pernah dipindai" in page.text
    first = await client.post("/settings/rescan", headers=post_headers(csrf))
    assert first.status_code == 200
    second = await client.post("/settings/rescan", headers=post_headers(csrf))
    assert second.status_code == 409
    assert "Menunggu" in (await client.get("/settings")).text
