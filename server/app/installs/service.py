"""Tambah container dengan satu perintah: token berbatas waktu, terikat ke satu mesin, dan mati setelah agen terhubung.

Token agen berumur panjang tidak masuk riwayat shell; script mengambilnya dari sini dan menulisnya ke .env di server.
User dan password database dibuat acak oleh script di server dan tidak pernah dikirim ke LumbungDB.
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agents_file import project_name
from app.agents.dependencies import registry
from app.backup_control.models import ControlLog
from app.containers import service as container_service
from app.core.config import settings
from app.core.database import db_now_plus
from app.installs.models import ContainerInstall

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "lmi_"
DEFAULT_PORT = 3306
_MACHINE_ID = re.compile(r"^[0-9a-f]{16,64}$")


class InstallError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def install_command(url: str, token: str) -> str:
    return f"curl -fsSL {url}/install.sh | LUMBUNG_URL={url} INSTALL_TOKEN={token} sh"


def env_text(name: str, token: str, url: str, port: int = DEFAULT_PORT, *, scripted: bool = False) -> str:
    """Isi .env. scripted=True: untuk script pasang, yang mengisi user dan password acak di server.

    Pasang manual tetap menyebut user_backup, karena container lama membawa user itu di volume-nya.
    """
    database, backup_user = "", "user_backup"
    if scripted:
        database = re.sub(r"[^A-Za-z0-9_]", "_", re.sub(r"[-_.]db$", "", name, flags=re.IGNORECASE))
        backup_user = ""
    return "\n".join((
        "### LumbungDB", f"LUMBUNG_URL={url}", f"LUMBUNG_TOKEN={token}",
        f"LUMBUNG_AGE_RECIPIENTS={settings.BACKUP_AGE_RECIPIENTS}", "",
        "### Image. Container lama: samakan versi MariaDB, mis. 11.4-a1",
        f"IMAGE_TAG={settings.IMAGE_TAG_DEFAULT}", "",
        "### Container", *([f"COMPOSE_PROJECT_NAME={project_name(name)}"] if scripted else []),
        f"CONTAINER_NAME={name}", f"CONTAINER_HOSTNAME={name}", f"DATA_PORT={port}", "",
        "### Dibuat script di server ini; LumbungDB tidak menyimpannya" if scripted
        else "### Isi sendiri di server; LumbungDB tidak menyimpannya",
        "DB_ROOTPASSWORD=", "DB_APPUSER=", "DB_APPPASS=", f"DB_NAME={database}",
        f"BACKUP_USER={backup_user}", "BACKUP_PASS=", "",
        "### Kosong = jadwal bawaan; bisa diubah dari dashboard", "BACKUP_CRON=", "DUMP_CRON=", "BINLOG_CRON=", "",
        "### Batas tahan arsip binlog yang belum terkirim, dalam hari; sesudahnya dihapus",
        "BINLOG_UNSENT_MAX_DAYS=3",
    ))


async def create_command(db: AsyncSession, name: str, port: int, actor: str) -> tuple[str, datetime]:
    """Membuat token pasang. Token sebelumnya untuk container ini langsung mati dan ikatan mesinnya lepas."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = db_now_plus(settings.INSTALL_COMMAND_HOURS * 3600)
    async with db.begin():
        stmt = insert(ContainerInstall).values(
            container=name, token_hash=_hash(token), port=port, created_by=actor, expires_at=expires,
        )
        await db.execute(stmt.on_duplicate_key_update(
            token_hash=stmt.inserted.token_hash, port=stmt.inserted.port, created_by=stmt.inserted.created_by,
            created_at=func.now(), expires_at=stmt.inserted.expires_at, machine_id=None, claimed_at=None,
        ))
        db.add(ControlLog(container=name, actor=actor, action="Buat perintah pasang", detail=f"Port {port}"))
        await db.flush()
        expires_at = await db.scalar(select(ContainerInstall.expires_at).where(ContainerInstall.container == name))
    return token, expires_at


async def register(db: AsyncSession, name: str, port: int, actor: str) -> tuple[str, str, datetime]:
    """Tambah container. Bila perintah gagal dibuat, baris agen ditarik lagi karena tokennya belum pernah tampil."""
    agent_token = await container_service.register(db, name)
    try:
        install_token, expires_at = await create_command(db, name, port, actor)
    except Exception:
        await container_service.cancel_pending(db, name)
        raise
    return agent_token, install_token, expires_at


async def get(db: AsyncSession, name: str) -> ContainerInstall | None:
    return await db.scalar(select(ContainerInstall).where(ContainerInstall.container == name))


async def renew_command(db: AsyncSession, name: str, actor: str) -> tuple[str, datetime]:
    """Perintah baru untuk container yang belum terhubung. Port mengikuti perintah sebelumnya."""
    if name not in registry.entries().active:
        raise InstallError(404, f"Agen {name} tidak aktif di agents.conf.")
    async with db.begin():
        if await container_service.has_heartbeat(db, name):
            raise InstallError(409, "Agen sudah terhubung, jadi perintah pasang tidak dibutuhkan lagi.")
        port = await db.scalar(select(ContainerInstall.port).where(ContainerInstall.container == name))
    return await create_command(db, name, port or DEFAULT_PORT, actor)


async def claim(db: AsyncSession, token: str | None, machine_id: str | None, url: str) -> str:
    """Dipanggil script: memeriksa token, mengikatnya ke mesin, dan menjawab isi .env."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        raise InstallError(401, "Token pasang tidak dikenal.")
    if not machine_id or not _MACHINE_ID.fullmatch(machine_id):
        raise InstallError(422, "machine-id server ini tidak terbaca.")
    expired = "Perintah pasang sudah kedaluwarsa. Buat perintah baru di halaman verifikasi container."
    async with db.begin():
        row = await db.scalar(
            select(ContainerInstall).where(ContainerInstall.token_hash == _hash(token)).with_for_update()
        )
        if row is None:
            raise InstallError(401, "Token pasang tidak dikenal atau sudah diganti perintah yang lebih baru.")
        if row.expires_at <= await db.scalar(select(func.now())):
            raise InstallError(410, expired)
        if await container_service.has_heartbeat(db, row.container):
            raise InstallError(410, f"{row.container} sudah terhubung ke LumbungDB; perintah ini tidak berlaku lagi.")
        if row.machine_id and row.machine_id != machine_id:
            raise InstallError(403, "Perintah ini sudah dipakai di mesin lain. Buat perintah baru bila ingin pindah.")
        agent_token = registry.entries().active.get(row.container)
        if agent_token is None:
            raise InstallError(410, f"Agen {row.container} tidak aktif di agents.conf.")
        name, port = row.container, row.port
        if row.machine_id is None:
            await db.execute(
                update(ContainerInstall).where(ContainerInstall.container == name)
                .values(machine_id=machine_id, claimed_at=func.now())
            )
            db.add(ControlLog(container=name, actor="script pasang", action="Jalankan perintah pasang",
                              detail=f"machine-id {machine_id}"))
    logger.info("install_claimed", extra={"container": name})
    return env_text(name, agent_token, url, port, scripted=True) + "\n"
