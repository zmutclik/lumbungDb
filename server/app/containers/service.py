import logging

from sqlalchemy import delete, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup_control.models import ControlLog
from app.containers.models import Container, Heartbeat
from app.agents.agents_file import NameRejected
from app.agents.dependencies import registry
from app.backups.models import Backup

logger = logging.getLogger(__name__)


async def ensure_row(db: AsyncSession, name: str, origin: str = "file") -> None:
    """Agen yang ditambahkan langsung di agents.conf dicatat saat pertama terlihat. Memakai transaksi pemanggil."""
    stmt = insert(Container).values(name=name, origin=origin)
    await db.execute(stmt.on_duplicate_key_update(name=stmt.inserted.name))


async def sync_with_file(db: AsyncSession) -> None:
    entries = registry.entries()
    async with db.begin():
        for name in sorted(entries.all_names):
            await ensure_row(db, name)
    await _drop_unlisted(db)


async def _drop_unlisted(db: AsyncSession) -> None:
    """Container yang barisnya dihapus dari agents.conf (bukan dikomentari) ikut dihapus dari database, selama
    belum pernah terhubung dan belum punya backup. Yang punya backup dibiarkan: NAS scan akan membuat barisnya lagi
    dari folder NAS, dan riwayatnya tidak boleh hilang. Dinonaktifkan lewat aplikasi = baris dikomentari, tetap ada."""
    if not registry.path.exists():
        return   # config/ tidak terpasang: jangan anggap semua container dihapus
    async with db.begin():
        names = set(await db.scalars(select(Container.name)))
    # File dibaca setelah daftar database: Tambah container menulis file sebelum database, jadi container yang
    # baru ditambahkan pasti sudah ada di file ini.
    unlisted = names - registry.entries().all_names
    for name in sorted(unlisted):
        try:
            async with db.begin():
                if await has_heartbeat(db, name) or await db.scalar(select(exists().where(Backup.container == name))):
                    continue
                await _forget(db, name)
        except IntegrityError:
            logger.info("container_unlisted_kept", extra={"container": name})   # masih dirujuk data lain
            continue
        logger.info("container_unlisted_removed", extra={"container": name})


async def _forget(db: AsyncSession, name: str) -> None:
    await db.execute(delete(ControlLog).where(ControlLog.container == name))   # hanya catatan perintah pasang
    await db.execute(delete(Container).where(Container.name == name))


async def known_names(db: AsyncSession) -> set[str]:
    async with db.begin():
        return set(await db.scalars(select(Container.name)))


async def register(db: AsyncSession, name: str, origin: str = "dashboard", source: str | None = None) -> str:
    """Menulis baris ke agents.conf lalu mencatat container. Nama dari riwayat yang barisnya sudah dihapus tetap
    dianggap terpakai, karena folder NAS-nya masih ada."""
    taken = await known_names(db)
    if any(other.lower() == name.lower() for other in taken):
        raise NameRejected(f"Nama {name} sudah terdaftar.")
    token = registry.add(name, extra_names=frozenset(taken))
    try:
        async with db.begin():
            db.add(Container(name=name, origin=origin, source_container=source))
    except Exception:
        registry.remove_pending(name)   # token tidak pernah ditampilkan, jadi baris tidak boleh tertinggal
        raise
    return token


async def has_heartbeat(db: AsyncSession, name: str) -> bool:
    return bool(await db.scalar(select(exists().where(Heartbeat.container == name))))


class AlreadyConnected(Exception):
    pass


async def cancel_pending(db: AsyncSession, name: str) -> bool:
    """Batalkan pendaftaran. Ditolak bila agen sudah pernah mengirim heartbeat. False bila tidak ada barisnya."""
    async with db.begin():
        if await has_heartbeat(db, name):
            raise AlreadyConnected
    if not registry.remove_pending(name):
        return False
    async with db.begin():
        await _forget(db, name)
    return True


async def machine_id(db: AsyncSession, name: str) -> str | None:
    return await db.scalar(select(Heartbeat.machine_id).where(Heartbeat.container == name))
