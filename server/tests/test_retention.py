"""Retensi: aturan murni, perlindungan bukti uji, dan penghapusan sungguhan di folder NAS uji."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.backups.models import Backup, IntegrityEvent
from app.containers.service import ensure_row
from app.core.config import settings
from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.retention import rules, service
from app.retention.models import RetentionLog
from app.runners.models import Job, JobFile, RestoreTest

NOW = datetime(2026, 11, 3, 12, 0)
POLICY = rules.Policy(30, 12, 3)


def item(id, days_ago, kind="fullbackup", database=None, container="a", **kw):
    return rules.Item(id, container, kind, database, NOW - timedelta(days=days_ago), **kw)


def doomed(items, passed=(), busy=(), unreviewed=()):
    keep = rules.protected_ids(items, set(passed), set(busy), set(unreviewed))
    return set(rules.plan(items, NOW, POLICY, keep))


def test_daily_window_keeps_everything_recent():
    items = [item(i, i) for i in range(1, 30)]
    assert doomed(items) == set()


def test_old_backups_keep_only_the_first_of_each_month():
    # Tiap hari sejak 1 Agustus sampai 3 Nov (> 30 hari lalu mulai 4 Okt)
    items, next_id = [], 1
    day = datetime(2026, 8, 1)
    while day < NOW:
        items.append(rules.Item(next_id, "a", "fullbackup", None, day + timedelta(hours=2)))
        next_id += 1
        day += timedelta(days=1)
    gone = doomed(items)
    kept = [i for i in items if i.id not in gone]
    old_kept = [i for i in kept if i.backup_at < NOW - timedelta(days=30)]
    assert [(i.backup_at.month, i.backup_at.day) for i in old_kept] == [(8, 1), (9, 1), (10, 1)]
    assert all(i.id in {k.id for k in kept} for i in items if i.backup_at >= NOW - timedelta(days=30))


def test_older_than_twelve_months_deleted_but_newest_is_protected():
    items = [item(1, 500), item(2, 450), item(3, 10)]
    assert doomed(items) == {1, 2}
    only = [item(1, 500)]
    assert doomed(only) == set()          # satu-satunya backup tidak pernah dihapus


def test_newest_per_database_is_protected_separately():
    items = [item(1, 400, kind="databases", database="x"), item(2, 400, kind="databases", database="y"),
             item(3, 5, kind="databases", database="y")]
    assert doomed(items) == {2}


def test_binlog_three_days_unless_in_protected_chain():
    full = item(1, 40, binlog_start="mysql-bin.000010", chain_id=1)
    items = [full, item(2, 20, "binlog", binlog_no=9, chain_id=1), item(3, 20, "binlog", binlog_no=10, chain_id=1),
             item(4, 10, "binlog", binlog_no=11, chain_id=1), item(5, 1, "binlog", binlog_no=12, chain_id=1),
             item(6, 20, "binlog", binlog_no=3, chain_id=0)]
    assert doomed(items) == {2, 3, 4, 6}       # tanpa bukti uji: full terbaru dilindungi, semua binlog > 3 hari dihapus
    # Dengan bukti uji: binlog dari nomor 10 ke atas dalam rantai yang sama ikut dilindungi
    assert doomed(items, passed={1}) == {2, 6}


def test_failed_newest_does_not_replace_last_passing_full():
    items = [item(1, 60, binlog_start="mysql-bin.000001", chain_id=1), item(2, 2, binlog_start="mysql-bin.000005", chain_id=2)]
    assert doomed(items, passed={1}) == set()       # keduanya dilindungi: terbaru, dan terakhir lolos uji
    assert doomed(items) == set()                   # tanpa bukti uji pun full itu masih yang pertama di bulannya
    old_month = [item(1, 60), item(2, 59), item(3, 2)]
    assert doomed(old_month) == {2}                 # yang kedua di bulan yang sama dihapus; terlindungi bila lolos uji
    assert doomed(old_month, passed={2}) == set()


def test_busy_and_unreviewed_are_protected():
    items = [item(1, 400), item(2, 380), item(3, 5)]
    assert doomed(items) == {1, 2}
    assert doomed(items, busy={1}, unreviewed={2}) == set()


def test_binlog_start_number_parsing():
    assert rules.binlog_start_number("mysql-bin.000042") == 42
    assert rules.binlog_start_number(None) is None
    assert rules.binlog_start_number("tanpa-angka") is None


# ---------- penghapusan sungguhan ----------
DATA = Path(settings.DATA_DIR)


@pytest.fixture
async def nas():
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))


async def add_backup(name, kind, relpath, days_ago, *, database=None, binlog_no=None, chain_id=None,
                     binlog_start=None, write=True, container="siak-db"):
    async with async_session_factory() as db, db.begin():
        await ensure_row(db, container)
        now = await db.scalar(select(func.now()))
        row = Backup(container=container, kind=kind, database=database, relpath=relpath,
                     stored_path=f"{kind}/{relpath}", size=10, sha256="a" * 64, plain_sha256=name * 64,
                     backup_at=now - timedelta(days=days_ago), binlog_no=binlog_no, chain_id=chain_id,
                     binlog_start=binlog_start)
        db.add(row)
        await db.flush()
        backup_id = row.id
    path = DATA / container / kind / relpath
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    return backup_id, path


async def run():
    async with async_session_factory() as db:
        return await service.run_retention(db, service.Deleter())


async def rows():
    async with async_session_factory() as db:
        return set(await db.scalars(select(Backup.id)))


async def test_old_files_deleted_from_disk_and_database_and_logged(nas):
    newest, newest_path = await add_backup("1", "fullbackup", "new.age", 1)
    old1, old1_path = await add_backup("2", "fullbackup", "old1.age", 400)
    old2, old2_path = await add_backup("3", "fullbackup", "old2.age", 390)
    binlog, binlog_path = await add_backup("4", "binlog", "mysql-bin.000001.gz.age", 5, binlog_no=1, chain_id=1)
    outcome = await run()
    assert outcome.deleted == 3 and outcome.freed == 30
    assert await rows() == {newest}
    assert newest_path.exists() and not old1_path.exists() and not old2_path.exists() and not binlog_path.exists()
    async with async_session_factory() as db:
        logged = {r.relpath: r.reason for r in await db.scalars(select(RetentionLog))}
    assert set(logged) == {"old1.age", "old2.age", "mysql-bin.000001.gz.age"}
    assert "binlog" in logged["mysql-bin.000001.gz.age"]


async def test_nothing_deleted_while_nas_has_a_problem(nas):
    await add_backup("1", "fullbackup", "new.age", 1)
    old, old_path = await add_backup("2", "fullbackup", "old.age", 400)
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("down"))
    outcome = await run()
    assert outcome.skipped == "NAS bermasalah" and old_path.exists() and old in await rows()


async def test_disabled_by_setting(nas, monkeypatch):
    monkeypatch.setattr(settings, "RETENTION_ENABLED", False)
    await add_backup("1", "fullbackup", "new.age", 1)
    _, old_path = await add_backup("2", "fullbackup", "old.age", 400)
    assert (await run()).skipped == "retensi dimatikan" and old_path.exists()


async def test_tested_full_and_its_chain_survive(nas):
    full, full_path = await add_backup("1", "fullbackup", "tested.age", 100, binlog_start="mysql-bin.000010", chain_id=1)
    await add_backup("2", "fullbackup", "newest.age", 1, chain_id=2, binlog_start="mysql-bin.000020")
    inside, inside_path = await add_backup("3", "binlog", "mysql-bin.000011.gz.age", 50, binlog_no=11, chain_id=1)
    before, before_path = await add_backup("4", "binlog", "mysql-bin.000009.gz.age", 50, binlog_no=9, chain_id=1)
    async with async_session_factory() as db, db.begin():
        job = Job(runner="r", container="siak-db", kind="restore_test", status="Lolos")
        db.add(job)
        await db.flush()
        db.add(RestoreTest(job_id=job.id, container="siak-db", runner="r", kind="full", backup_id=full, status="Lolos"))
    await run()
    assert full_path.exists() and inside_path.exists() and not before_path.exists()
    assert before not in await rows() and {full, inside} <= await rows()


async def test_files_of_unfinished_jobs_and_unreviewed_versions_survive(nas):
    await add_backup("1", "fullbackup", "new.age", 1)
    busy, busy_path = await add_backup("2", "fullbackup", "busy.age", 400)
    first, first_path = await add_backup("3", "fullbackup", "dup-a.age", 401)
    second, second_path = await add_backup("4", "fullbackup", "dup-b.age", 402)
    async with async_session_factory() as db, db.begin():
        job = Job(runner="r", container="siak-db", kind="restore_test", status="Menunggu")
        db.add(job)
        await db.flush()
        db.add(JobFile(job_id=job.id, backup_id=busy))
        db.add(IntegrityEvent(container="siak-db", kind="fullbackup", relpath="dup", first_backup_id=first,
                              new_backup_id=second))
    outcome = await run()
    assert outcome.deleted == 0 and busy_path.exists() and first_path.exists() and second_path.exists()


async def test_references_are_cleaned_when_a_file_is_deleted(nas):
    await add_backup("1", "fullbackup", "new.age", 1)
    old, old_path = await add_backup("2", "fullbackup", "old.age", 400)
    async with async_session_factory() as db, db.begin():
        job = Job(runner="r", container="siak-db", kind="restore_test", status="Gagal")
        db.add(job)
        await db.flush()
        db.add(JobFile(job_id=job.id, backup_id=old))
        db.add(RestoreTest(job_id=job.id, container="siak-db", runner="r", kind="full", backup_id=old, status="Gagal"))
    assert (await run()).deleted == 1
    async with async_session_factory() as db:
        assert (await db.scalars(select(RestoreTest.backup_id))).all() == [None]      # riwayat hasil tetap ada
        assert (await db.scalars(select(JobFile))).all() == []


async def test_missing_file_still_cleans_the_row_and_failures_keep_it(nas):
    await add_backup("1", "fullbackup", "new.age", 1)
    gone, _ = await add_backup("2", "fullbackup", "gone.age", 400, write=False)
    stuck, stuck_path = await add_backup("3", "fullbackup", "stuck.age", 401)
    stuck_path.unlink()
    stuck_path.mkdir()          # bukan file biasa → penghapus menolak
    outcome = await run()
    assert outcome.deleted == 1 and outcome.failed == 1
    remaining = await rows()
    assert gone not in remaining and stuck in remaining and stuck_path.is_dir()


async def test_hung_deleter_skips_and_keeps_rows(nas, monkeypatch):
    await add_backup("1", "fullbackup", "new.age", 1)
    old, old_path = await add_backup("2", "fullbackup", "old.age", 400)

    class Hung(service.Deleter):
        async def delete(self, paths):
            return None

    async with async_session_factory() as db:
        outcome = await service.run_retention(db, Hung())
    assert outcome.skipped == "penghapus tidak merespons" and old in await rows() and old_path.exists()


def test_deleter_script_refuses_paths_outside_root(tmp_path):
    import json
    import subprocess
    import sys
    (tmp_path / "root" / "a" / "binlog").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("jangan dihapus")
    (tmp_path / "root" / "a" / "binlog" / "link").symlink_to(victim)
    request = {"root": str(tmp_path / "root"),
               "paths": ["../victim", "a/binlog/../../../victim", "/etc/passwd", "a/binlog/link", "a/b", "a/binlog/.x.part"]}
    out = subprocess.run([sys.executable, "-I", str(service.DELETER)], input=json.dumps(request), capture_output=True,
                         text=True, check=True).stdout
    result = json.loads(out)
    assert result["deleted"] == [] and len(result["failed"]) == 6 and victim.exists()
