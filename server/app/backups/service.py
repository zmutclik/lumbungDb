from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC

import anyio
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.backups import naming, storage
from app.backups.models import Backup, IntegrityEvent
from app.containers.models import Container
from app.containers.service import ensure_row
from app.core.config import settings

PAGE_SIZE = 30
# Total dibagi rata per proses web. Upload yang tersangkut hanya memakai jatah ini, tidak menghabiskan worker dashboard.
PROCESS_SLOTS = max(1, -(-settings.UPLOAD_MAX_CONCURRENT // max(1, settings.WEB_CONCURRENCY)))
_fs_limiter = anyio.CapacityLimiter(PROCESS_SLOTS)


class UploadRejected(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class UploadResult:
    result: str               # stored | exists | version
    stored_path: str | None


async def _fs(function, *args):
    return await anyio.to_thread.run_sync(function, *args, limiter=_fs_limiter, abandon_on_cancel=True)


async def receive_upload(
    db: AsyncSession, container: str, path: naming.UploadPath, stream: AsyncIterable[bytes], *,
    expected_sha256: str | None, mtime_header: str | None, request_id: int | None, binlog_start: str | None,
) -> UploadResult:
    """Stream ke .part → fsync → cek checksum → nama akhir → metadata. Tanpa transaksi terbuka selama I/O NAS."""
    part = await _fs(storage.open_part, container, path.kind, path.relpath)
    try:
        async for chunk in stream:
            if chunk:
                await _fs(storage.write_chunk, part, chunk)
        sha256 = await _fs(storage.finish_part, part)
        if part.size == 0:
            raise UploadRejected(422, "File kosong.")
        if expected_sha256 and expected_sha256.lower() != sha256:
            raise UploadRejected(422, "Checksum tidak cocok.")

        async with db.begin():
            now = await db.scalar(select(func.now()))
            existing = list((await db.execute(
                select(Backup.id, Backup.sha256, Backup.chain_id).where(
                    Backup.container == container, Backup.kind == path.kind, Backup.relpath == path.relpath,
                ).order_by(Backup.id)
            )).all())
        if any(row.sha256 == sha256 for row in existing):
            await _fs(storage.discard_part, part)
            return UploadResult("exists", None)

        backup_at = naming.backup_time(path, mtime_header, now)
        mtime = backup_at.replace(tzinfo=UTC).timestamp()
        target, _version = await _fs(storage.place_part, part, sha256, len(existing) + 1, mtime)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await _fs(storage.discard_part, part)
        raise

    stored_path = str(target.relative_to(storage.container_root(container)))
    try:
        async with db.begin():
            await ensure_row(db, container)
            # Satu penulis rantai per container: upload paralel tidak menghitung rantai dari data yang berbeda
            await db.execute(select(Container.name).where(Container.name == container).with_for_update())
            row = Backup(
                container=container, kind=path.kind, database=path.database, relpath=path.relpath,
                stored_path=stored_path, size=part.size, sha256=sha256, backup_at=backup_at, request_id=request_id,
                binlog_no=path.binlog_no, binlog_start=binlog_start if path.kind == "fullbackup" else None,
            )
            db.add(row)
            await db.flush()
            if path.kind in ("binlog", "fullbackup"):
                await _recompute_chains(db, container)
                await db.refresh(row, ["chain_id"])
            if existing and _needs_review(path, existing, row):
                db.add(IntegrityEvent(
                    container=container, kind=path.kind, relpath=path.relpath,
                    first_backup_id=existing[0].id, new_backup_id=row.id,
                ))
    except IntegrityError:
        return UploadResult("exists", None)   # upload bersamaan dengan isi sama sudah lebih dulu tercatat
    return UploadResult("version" if existing else "stored", stored_path)


def _needs_review(path: naming.UploadPath, existing: list, row: Backup) -> bool:
    """Binlog yang masuk rantai berbeda adalah kejadian normal setelah restore."""
    if path.kind != "binlog":
        return True
    return any(previous.chain_id == row.chain_id for previous in existing)


async def _recompute_chains(db: AsyncSession, container: str) -> None:
    """Urut menurut backup_at lalu nomor; nomor yang lebih kecil dari baris sebelumnya memulai rantai baru.
    Dihitung ulang dari awal, jadi binlog lama yang terlambat terkirim tetap masuk rantai lamanya."""
    rows = (await db.execute(
        select(Backup.id, Backup.binlog_no, Backup.chain_id, Backup.backup_at)
        .where(Backup.container == container, Backup.kind == "binlog")
        .order_by(Backup.backup_at, Backup.binlog_no, Backup.id)
    )).all()
    chain = 1
    previous = None
    chains: list[tuple[int, int, object]] = []   # (nomor, rantai, backup_at), urut waktu
    for row in rows:
        if previous is not None and row.binlog_no < previous:
            chain += 1
        previous = row.binlog_no
        chains.append((row.binlog_no, chain, row.backup_at))
        if row.chain_id != chain:
            await db.execute(update(Backup).where(Backup.id == row.id).values(chain_id=chain))

    # Rantai sebuah full: rantai yang memiliki nomor binlog_start dengan backup_at sesudah full dibuat.
    fulls = (await db.execute(
        select(Backup.id, Backup.binlog_start, Backup.chain_id, Backup.backup_at)
        .where(Backup.container == container, Backup.kind == "fullbackup", Backup.binlog_start.is_not(None))
    )).all()
    for full in fulls:
        number = naming.binlog_start_number(full.binlog_start)
        found = next((c for no, c, at in chains if no == number and at >= full.backup_at), None)
        if found != full.chain_id:
            await db.execute(update(Backup).where(Backup.id == full.id).values(chain_id=found))


async def list_files(
    db: AsyncSession, container: str, *, kind: str | None = None, query: str | None = None,
    day_range: tuple | None = None, before_id: int | None = None, limit: int = PAGE_SIZE,
) -> tuple[list[Backup], int | None]:
    stmt = select(Backup).where(Backup.container == container).order_by(Backup.id.desc()).limit(limit + 1)
    if kind:
        stmt = stmt.where(Backup.kind == kind)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Backup.relpath.like(f"%{escaped}%", escape="\\"))
    if day_range:
        stmt = stmt.where(Backup.backup_at >= day_range[0], Backup.backup_at < day_range[1])
    if before_id is not None:
        stmt = stmt.where(Backup.id < before_id)
    rows = list(await db.scalars(stmt))
    return rows[:limit], (rows[limit - 1].id if len(rows) > limit else None)


async def integrity_for(db: AsyncSession, container: str) -> list[dict]:
    """Tiap perbedaan beserta versi pertama dan versi barunya, yang belum ditinjau lebih dulu."""
    first = Backup.__table__.alias("first")
    new = Backup.__table__.alias("new")
    rows = (await db.execute(
        select(
            IntegrityEvent.id, IntegrityEvent.kind, IntegrityEvent.relpath, IntegrityEvent.created_at,
            IntegrityEvent.reviewed_by, IntegrityEvent.reviewed_at,
            first.c.size.label("first_size"), first.c.sha256.label("first_sha"),
            first.c.received_at.label("first_at"), first.c.stored_path.label("first_path"),
            new.c.size.label("new_size"), new.c.sha256.label("new_sha"), new.c.stored_path.label("new_path"),
        )
        .join(first, first.c.id == IntegrityEvent.first_backup_id)
        .join(new, new.c.id == IntegrityEvent.new_backup_id)
        .where(IntegrityEvent.container == container)
        .order_by(IntegrityEvent.reviewed_at.is_not(None), IntegrityEvent.id.desc())
        .limit(50)
    )).all()
    return [dict(row._mapping) for row in rows]


async def review_integrity(db: AsyncSession, event_id: int, reviewer: str) -> str | None:
    """Mengembalikan nama container, atau None bila tidak ada."""
    async with db.begin():
        event = await db.get(IntegrityEvent, event_id)
        if event is None:
            return None
        if event.reviewed_at is None:
            event.reviewed_by = reviewer
            event.reviewed_at = await db.scalar(select(func.now()))
        return event.container
