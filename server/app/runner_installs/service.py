"""Pasang runner dengan satu perintah, seperti Tambah container: token berbatas waktu, terikat ke satu mesin, dan mati
setelah runner mengirim heartbeat.

Token runner berumur panjang tidak masuk riwayat shell; script mengambilnya dari sini dan menulisnya ke .env di mesin runner.
Kunci privat age tidak pernah lewat sini: script hanya memeriksa kunci yang sudah ada di mesin runner.
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import db_now_plus
from app.runner_installs.models import RunnerInstall
from app.runners.dependencies import registry
from app.runners.models import RunnerHeartbeat

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "lmn_"
RUNNER_IMAGE = "zmutclik/lumbungdb-runner"
MARIADB_IMAGE = "zmutclik/mariadb:lumbungdb-"
SCRATCH_DIR = "/srv/lumbung-scratch"
AGE_IDENTITY_FILE = "/etc/lumbung/age-runner.key"
_MACHINE_ID = re.compile(r"^[0-9a-f]{16,64}$")


class InstallError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def install_command(url: str, token: str) -> str:
    return f"curl -fsSL {url}/install-runner.sh | LUMBUNG_URL={url} INSTALL_TOKEN={token} sh"


def env_text(token: str, url: str) -> str:
    """Isi .env di mesin runner, juga untuk pasang manual."""
    return "\n".join((
        f"LUMBUNG_URL={url}", f"LUMBUNG_RUNNER_TOKEN={token}",
        f"RUNNER_IMAGE={RUNNER_IMAGE}:{settings.RUNNER_VERSION_LATEST}",
        f"SCRATCH_DIR={SCRATCH_DIR}", f"AGE_IDENTITY_FILE={AGE_IDENTITY_FILE}",
        f"MARIADB_IMAGE={MARIADB_IMAGE}{settings.IMAGE_TAG_DEFAULT}",
    ))


async def _has_heartbeat(db: AsyncSession, name: str) -> bool:
    return await db.scalar(select(RunnerHeartbeat.runner).where(RunnerHeartbeat.runner == name)) is not None


async def create_command(db: AsyncSession, name: str, actor: str) -> tuple[str, datetime]:
    """Membuat token pasang. Token sebelumnya untuk runner ini langsung mati dan ikatan mesinnya lepas."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = db_now_plus(settings.INSTALL_COMMAND_HOURS * 3600)
    async with db.begin():
        stmt = insert(RunnerInstall).values(runner=name, token_hash=_hash(token), created_by=actor, expires_at=expires)
        await db.execute(stmt.on_duplicate_key_update(
            token_hash=stmt.inserted.token_hash, created_by=stmt.inserted.created_by,
            created_at=func.now(), expires_at=stmt.inserted.expires_at, machine_id=None, claimed_at=None,
        ))
        await db.flush()
        expires_at = await db.scalar(select(RunnerInstall.expires_at).where(RunnerInstall.runner == name))
    logger.info("runner_install_command", extra={"runner": name, "actor": actor})
    return token, expires_at


async def renew_command(db: AsyncSession, name: str, actor: str) -> tuple[str, datetime]:
    """Perintah baru untuk runner yang belum terhubung."""
    if name not in registry.entries().active:
        raise InstallError(404, f"Runner {name} tidak aktif di runners.conf.")
    async with db.begin():
        if await _has_heartbeat(db, name):
            raise InstallError(409, "Runner sudah terhubung, jadi perintah pasang tidak dibutuhkan lagi.")
    return await create_command(db, name, actor)


async def forget(db: AsyncSession, name: str) -> None:
    """Runner dinonaktifkan: perintah pasangnya ikut mati."""
    async with db.begin():
        await db.execute(delete(RunnerInstall).where(RunnerInstall.runner == name))


async def claim(db: AsyncSession, token: str | None, machine_id: str | None, url: str) -> str:
    """Dipanggil script: memeriksa token, mengikatnya ke mesin, dan menjawab isi .env runner.

    Baris LUMBUNG_AGE_RECIPIENTS hanya untuk script, supaya bisa memastikan kunci di mesin runner memang penerima backup.
    """
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        raise InstallError(401, "Token pasang tidak dikenal.")
    if not machine_id or not _MACHINE_ID.fullmatch(machine_id):
        raise InstallError(422, "machine-id server ini tidak terbaca.")
    async with db.begin():
        row = await db.scalar(select(RunnerInstall).where(RunnerInstall.token_hash == _hash(token)).with_for_update())
        if row is None:
            raise InstallError(401, "Token pasang tidak dikenal atau sudah diganti perintah yang lebih baru.")
        if row.expires_at <= await db.scalar(select(func.now())):
            raise InstallError(410, "Perintah pasang sudah kedaluwarsa. Buat perintah baru di halaman Runner.")
        if await _has_heartbeat(db, row.runner):
            raise InstallError(410, f"Runner {row.runner} sudah terhubung ke LumbungDB; perintah ini tidak berlaku lagi.")
        if row.machine_id and row.machine_id != machine_id:
            raise InstallError(403, "Perintah ini sudah dipakai di mesin lain. Buat perintah baru bila ingin pindah.")
        runner_token = registry.entries().active.get(row.runner)
        if runner_token is None:
            raise InstallError(410, f"Runner {row.runner} tidak aktif di runners.conf.")
        name = row.runner
        if row.machine_id is None:
            await db.execute(update(RunnerInstall).where(RunnerInstall.runner == name)
                             .values(machine_id=machine_id, claimed_at=func.now()))
    logger.info("runner_install_claimed", extra={"runner": name})
    return (f"RUNNER_NAME={name}\n" + env_text(runner_token, url)
            + f"\nLUMBUNG_AGE_RECIPIENTS={settings.BACKUP_AGE_RECIPIENTS}\n")
