"""Pindai ulang NAS. Dijalankan worker; permintaannya dari halaman Pengaturan. Tidak mengubah file di NAS.

Yang tidak bisa dipulihkan dari file: checksum isi sebelum enkripsi (diganti checksum file, jadi kiriman ulang dari
agen menjadi versi baru), `binlog_start` full, dan ID permintaan backup."""
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry
from app.backups import naming
from app.backups.models import Backup
from app.backups.service import _recompute_chains
from app.containers.service import ensure_row
from app.core.config import settings
from app.nas import service as nas_service
from app.nas.models import NasScan

logger = logging.getLogger(__name__)
SCANNER = Path(__file__).with_name("scan_files.py")
VERSION_SUFFIX = re.compile(r"\.v\d+-[0-9a-f]{8}$")


class ScanError(Exception):
    pass


async def request_scan(db: AsyncSession, actor: str) -> NasScan | None:
    """None bila sudah ada pindaian yang menunggu atau berjalan."""
    async with db.begin():
        active = await db.scalar(select(NasScan.id).where(NasScan.status.in_(("Menunggu", "Berjalan"))).limit(1))
        if active is not None:
            return None
        scan = NasScan(requested_by=actor)
        db.add(scan)
        await db.flush()
    return scan


async def recent_scans(db: AsyncSession, limit: int = 5) -> list[NasScan]:
    return list(await db.scalars(select(NasScan).order_by(NasScan.id.desc()).limit(limit)))


def to_backup(record: dict, now: datetime) -> Backup | None:
    """Baris Backup dari satu file di NAS, atau None bila namanya bukan hasil upload LumbungDB."""
    stored = record["stored_path"]
    parts = stored.split("/")
    relative = VERSION_SUFFIX.sub("", "/".join(parts[1:]))
    try:
        path = naming.parse_upload_path(record["kind"], relative)
    except naming.InvalidPath:
        return None
    if record["kind"] == "databases" and record["database"] != path.database:
        return None
    backup_at = naming.backup_time(path, str(record["mtime"]), now)
    return Backup(
        container=record["container"], kind=path.kind, database=path.database, relpath=path.relpath,
        stored_path=stored, size=record["size"], sha256=record["sha256"], plain_sha256=record["sha256"],
        backup_at=backup_at, binlog_no=path.binlog_no)


async def _read_records(stall_seconds: int):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(SCANNER), settings.DATA_DIR, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=1 << 20)
    try:
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), stall_seconds)
            except TimeoutError as err:
                raise ScanError("NAS tidak merespons saat dipindai") from err
            if not line:
                raise ScanError("pemindai berhenti sebelum selesai")
            record = json.loads(line)
            if record.get("done"):
                return
            yield record
    finally:
        if process.returncode is None:
            process.kill()      # bila proses tersangkut di D state, kill menunggu NFS; biarkan di latar belakang


async def run_pending(db: AsyncSession) -> NasScan | None:
    async with db.begin():
        scan = await db.scalar(select(NasScan).where(NasScan.status == "Menunggu").order_by(NasScan.id).limit(1)
                               .with_for_update(skip_locked=True))
        if scan is None:
            return None
        nas = await nas_service.get_view(db)
        if nas.problem:
            return None
        scan_id = scan.id
        scan.status, scan.started_at = "Berjalan", func.now()
        now = await db.scalar(select(func.now()))
    seen = added = skipped = 0
    note = None
    status = "Selesai"
    known_names = registry.entries().all_names
    touched: set[str] = set()
    try:
        async for record in _read_records(settings.NAS_SCAN_STALL_SECONDS):
            seen += 1
            row = to_backup(record, now) if record["container"] in known_names else None
            if row is None:
                skipped += 1
                continue
            async with db.begin():
                if await db.scalar(select(Backup.id).where(Backup.container == row.container,
                                                           Backup.stored_path == row.stored_path).limit(1)):
                    continue
                await ensure_row(db, row.container)
                db.add(row)
                added += 1
                touched.add(row.container)
        for container in sorted(touched):
            async with db.begin():
                await _recompute_chains(db, container)
    except ScanError as err:
        status, note = "Gagal", str(err)
    except Exception as err:      # bukan kesalahan NAS: tetap dicatat, pindaian tidak dianggap selesai
        logger.exception("nas_scan_failed")
        status, note = "Gagal", f"{type(err).__name__}"
    async with db.begin():
        await db.execute(NasScan.__table__.update().where(NasScan.id == scan_id).values(
            status=status, finished_at=func.now(), seen=seen, added=added, skipped=skipped, note=note))
    return scan


_task: asyncio.Task | None = None


async def kick(factory) -> None:
    """Memulai pindaian yang menunggu sebagai tugas latar belakang, satu pada satu waktu, supaya pengecek alert tidak
    ikut menunggu pindaian yang lama."""
    global _task
    if _task is not None and not _task.done():
        return

    async def work() -> None:
        async with factory() as db:
            await run_pending(db)

    async with factory() as db:
        waiting = await db.scalar(select(NasScan.id).where(NasScan.status == "Menunggu").limit(1))
    if waiting is not None:
        _task = asyncio.create_task(work())


async def reset_interrupted(factory) -> None:
    """Worker hanya satu instance: pindaian yang masih Berjalan saat worker mulai berarti worker sebelumnya mati."""
    async with factory() as db, db.begin():
        await db.execute(NasScan.__table__.update().where(NasScan.status == "Berjalan").values(
            status="Gagal", finished_at=func.now(), note="Worker dimulai ulang saat pindaian berjalan"))
