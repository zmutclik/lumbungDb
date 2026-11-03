"""Menjalankan retensi: merencanakan dari database, menghapus file lewat proses kecil, lalu menghapus barisnya.
File dihapus lebih dulu, baris sesudahnya: bila penghapusan file gagal, barisnya tetap ada dan dicoba lagi."""
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.backups.models import Backup, IntegrityEvent
from app.core.config import settings
from app.nas import service as nas_service
from app.retention import rules
from app.retention.models import RetentionLog
from app.runners.models import Job, JobDownload, JobFile, RestoreTest

logger = logging.getLogger(__name__)
DELETER = Path(__file__).with_name("delete_files.py")
UNFINISHED = ("Menunggu", "Berjalan")


@dataclass
class Outcome:
    skipped: str | None = None
    planned: int = 0
    deleted: int = 0
    failed: int = 0
    freed: int = 0


class Deleter:
    """Satu proses penghapus. Bila tersangkut (NFS macet), prosesnya dibiarkan dan putaran berikutnya dilewati."""

    def __init__(self, data_dir: str | None = None, timeout: float | None = None) -> None:
        self.data_dir = data_dir if data_dir is not None else settings.DATA_DIR
        self.timeout = timeout if timeout is not None else settings.RETENTION_TIMEOUT_SECONDS
        self.process: asyncio.subprocess.Process | None = None

    @property
    def busy(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def delete(self, paths: list[str]) -> dict | None:
        if self.busy:
            return None
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(DELETER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        payload = json.dumps({"root": self.data_dir, "paths": paths}).encode()
        try:
            out, _ = await asyncio.wait_for(self.process.communicate(payload), self.timeout)
        except TimeoutError:
            logger.warning("retention_deleter_hang")
            return None
        if self.process.returncode != 0:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None


async def _load_plan(db: AsyncSession) -> tuple[dict[int, str], dict[int, Backup]]:
    rows = list(await db.scalars(select(Backup)))
    items = [rules.Item(b.id, b.container, b.kind, b.database, b.backup_at, b.binlog_no, b.chain_id, b.binlog_start)
             for b in rows]
    passed = set(await db.scalars(select(RestoreTest.backup_id).where(
        RestoreTest.status == "Lolos", RestoreTest.backup_id.is_not(None))))
    busy = set(await db.scalars(
        select(JobFile.backup_id).join(Job, Job.id == JobFile.job_id).where(Job.status.in_(UNFINISHED))))
    unreviewed = set(await db.scalars(select(IntegrityEvent.first_backup_id).where(IntegrityEvent.reviewed_at.is_(None))))
    unreviewed |= set(await db.scalars(select(IntegrityEvent.new_backup_id).where(IntegrityEvent.reviewed_at.is_(None))))
    now = await db.scalar(select(func.now()))
    policy = rules.Policy(settings.RETENTION_DAILY_DAYS, settings.RETENTION_MONTHS, settings.RETENTION_BINLOG_DAYS)
    keep = rules.protected_ids(items, passed, busy, unreviewed)
    doomed = rules.plan(items, now, policy, keep)
    return doomed, {b.id: b for b in rows if b.id in doomed}


async def run_retention(db: AsyncSession, deleter: Deleter) -> Outcome:
    if not settings.RETENTION_ENABLED:
        return Outcome(skipped="retensi dimatikan")
    if deleter.busy:
        return Outcome(skipped="penghapus sebelumnya masih berjalan")
    async with db.begin():
        nas = await nas_service.get_view(db)
        if nas.problem:
            return Outcome(skipped="NAS bermasalah")
        doomed, rows = await _load_plan(db)
    outcome = Outcome(planned=len(doomed))
    batch = sorted(rows.values(), key=lambda b: (b.backup_at, b.id))[: settings.RETENTION_BATCH]
    if not batch:
        return outcome
    # Nilai biasa, bukan objek ORM, karena transaksi berikutnya mengosongkan objek.
    plan = [(b.id, b.container, b.kind, b.relpath, b.stored_path, b.size, b.backup_at, doomed[b.id]) for b in batch]
    result = await deleter.delete([f"{container}/{stored}" for _, container, _, _, stored, *_ in plan])
    if result is None:
        outcome.skipped = "penghapus tidak merespons"
        return outcome
    gone = set(result["deleted"]) | set(result["missing"])
    outcome.failed = len(result["failed"])
    done = [p for p in plan if f"{p[1]}/{p[4]}" in gone]
    if not done:
        return outcome
    ids = [p[0] for p in done]
    async with db.begin():
        await db.execute(delete(JobDownload).where(JobDownload.backup_id.in_(ids)))
        await db.execute(delete(JobFile).where(JobFile.backup_id.in_(ids)))
        await db.execute(update(RestoreTest).where(RestoreTest.backup_id.in_(ids)).values(backup_id=None))
        await db.execute(delete(IntegrityEvent).where(
            IntegrityEvent.reviewed_at.is_not(None),
            (IntegrityEvent.first_backup_id.in_(ids)) | (IntegrityEvent.new_backup_id.in_(ids))))
        await db.execute(delete(Backup).where(Backup.id.in_(ids)))
        for _, container, kind, relpath, _stored, size, backup_at, reason in done:
            db.add(RetentionLog(container=container, kind=kind, relpath=relpath, size=size, backup_at=backup_at,
                                reason=reason))
    outcome.deleted = len(done)
    outcome.freed = sum(p[5] for p in done)
    logger.info("retention_done", extra={"deleted": outcome.deleted, "freed": outcome.freed})
    return outcome
