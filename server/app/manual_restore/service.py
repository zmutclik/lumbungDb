"""Restore manual: sesi dengan token berbatas waktu, terikat ke satu mesin, untuk memulihkan seluruh container ke
server mana pun lewat satu perintah. Token tampil sekali; yang tersimpan hanya hash-nya. Password database baru dibuat
script di server tujuan dan tidak pernah dikirim ke LumbungDB."""
import hashlib
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agents_file import project_name
from app.agents.dependencies import registry as agents
from app.backup_control.models import BackupRequest, ControlLog
from app.backups.models import Backup
from app.containers.models import Container, Heartbeat
from app.core.config import settings
from app.core.database import db_now_plus, db_time_plus
from app.manual_restore.models import ManualRestore, ManualRestoreDownload, ManualRestoreFile
from app.nas import service as nas_service
from app.runners import promote
from app.runners.models import ContainerSettings

TOKEN_PREFIX = "lmr_"
HOURS_CHOICES = (2, 4, 8, 24)
EXTEND_SECONDS = 2 * 3600
LIVE = ("Aktif", "Berjalan", "Gagal")          # status yang masih boleh dipakai script, selama belum kedaluwarsa
_MACHINE_ID = re.compile(r"^[0-9a-f]{16,64}$")
_IMAGE_TAG = re.compile(r"^[0-9]+\.[0-9]+-[A-Za-z0-9.]+$")
MIN_PORT = 1024


class RestoreError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def command(url: str, token: str) -> str:
    return (f"curl -fsSL {url}/restore.sh | LUMBUNG_URL={url} RESTORE_TOKEN={token} "
            "AGE_IDENTITY_FILE=/path/ke/kunci-pemulihan.key sh")


# ---------- pembatas percobaan token salah ----------
_FAIL_WINDOW, _FAIL_LIMIT = 600, 10
_failures: dict[str, deque] = defaultdict(deque)       # per proses web, seperti batas unduhan


def _check_not_blocked(ip: str) -> None:
    now = time.monotonic()
    recent = _failures[ip]
    while recent and now - recent[0] > _FAIL_WINDOW:
        recent.popleft()
    if len(recent) >= _FAIL_LIMIT:
        raise RestoreError(429, "Terlalu banyak token salah dari alamat ini. Coba lagi beberapa menit lagi.")


def _record_failure(ip: str) -> None:
    _failures[ip].append(time.monotonic())


# ---------- pembuatan dan pengelolaan sesi (dashboard) ----------
async def full_choices(db: AsyncSession, source: str) -> list[dict]:
    """Semua full yang tersimpan beserta buktinya. Yang terbaru lolos uji dipilih lebih dulu di form."""
    from app.runners.models import RestoreTest
    fulls = list(await db.scalars(
        select(Backup).where(Backup.container == source, Backup.kind == "fullbackup")
        .order_by(Backup.backup_at.desc(), Backup.id.desc()).limit(60)))
    rows = (await db.execute(
        select(RestoreTest.backup_id, RestoreTest.status).where(
            RestoreTest.container == source, RestoreTest.kind == "full", RestoreTest.backup_id.is_not(None))
        .order_by(RestoreTest.id))).all()
    proof: dict[int, str] = {}
    for backup_id, status in rows:         # hasil terakhir menang; "Tidak terlaksana" tidak mengubah bukti
        if status in ("Lolos", "Gagal"):
            proof[backup_id] = status
    chain_counts = dict((await db.execute(
        select(Backup.chain_id, func.count()).where(Backup.container == source, Backup.kind == "binlog")
        .group_by(Backup.chain_id))).all())
    choices = [{"id": f.id, "backup_at": f.backup_at, "size": f.size, "proof": proof.get(f.id, "Belum diuji"),
                "binlogs": chain_counts.get(f.chain_id, 0) if f.chain_id is not None else 0} for f in fulls]
    default = next((c["id"] for c in choices if c["proof"] == "Lolos"), choices[0]["id"] if choices else None)
    return [{**c, "default": c["id"] == default} for c in choices]


async def sessions_for(db: AsyncSession, source: str, limit: int = 15) -> list[dict]:
    now = await db.scalar(select(func.now()))
    rows = list(await db.scalars(
        select(ManualRestore).where(ManualRestore.source == source).order_by(ManualRestore.id.desc()).limit(limit)))
    source_machine = await db.scalar(select(Heartbeat.machine_id).where(Heartbeat.container == source))
    out = []
    for row in rows:
        live = row.status in LIVE and row.expires_at > now
        status = "Kedaluwarsa" if row.status in LIVE and not live else row.status
        out.append({"id": row.id, "new_name": row.new_name, "port": row.port, "status": status, "live": live,
                    "step": row.step, "note": row.note, "created_by": row.created_by, "created_at": row.created_at,
                    "expires_at": row.expires_at, "extendable": live and row.expires_at < row.max_expires_at,
                    "same_server": bool(row.machine_id) and row.machine_id == source_machine,
                    "binlog": row.binlog, "bound": bool(row.machine_id)})
    return out


async def create(db: AsyncSession, source: str, full_id: int, binlog: str, new_name: str, port: int, hours: int,
                 actor: str) -> tuple[ManualRestore, str]:
    """RestoreError untuk semua penolakan; NameRejected tidak dipakai di sini, supaya satu jenis kesalahan."""
    if binlog not in ("all", "none"):
        raise RestoreError(422, "Pilih binlog: sampai data terbaru, atau full saja.")
    if hours not in HOURS_CHOICES or hours > settings.MANUAL_RESTORE_MAX_HOURS:
        raise RestoreError(422, "Batas waktu harus 2, 4, 8, atau 24 jam.")
    if not MIN_PORT <= port <= 65535:
        raise RestoreError(422, f"Port harus antara {MIN_PORT} dan 65535.")
    if not agents.writable():
        raise RestoreError(500, "config/agents.conf tidak bisa ditulis; sesi tidak dibuat. Perbaiki izin folder config.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    async with db.begin():
        nas = await nas_service.get_view(db)
        if nas.problem:
            raise RestoreError(503, "NAS bermasalah; file tidak bisa diunduh sekarang.")
        if source not in set(await db.scalars(select(Container.name))):
            raise RestoreError(404, f"Container {source} tidak ada.")
        full = await db.get(Backup, full_id)
        if full is None or full.container != source or full.kind != "fullbackup":
            raise RestoreError(422, "Full yang dipilih bukan milik container ini.")
        error = agents.name_error(new_name)
        taken = set(await db.scalars(select(Container.name)))
        reserved = set(await db.scalars(select(ManualRestore.new_name).where(
            ManualRestore.status.in_(LIVE), ManualRestore.expires_at > func.now())))
        for other in taken | reserved:
            if other.lower() == new_name.lower() or project_name(other) == project_name(new_name):
                error = error or f"Nama {new_name} sudah terdaftar atau dicadangkan sesi lain."
        if error:
            raise RestoreError(422, error)
        ids = [full.id]
        start = promote_binlog_start(full.binlog_start)
        if binlog == "all" and full.chain_id is not None and start is not None:
            ids += list(await db.scalars(
                select(Backup.id).where(Backup.container == source, Backup.kind == "binlog",
                                        Backup.chain_id == full.chain_id, Backup.binlog_no >= start)
                .order_by(Backup.binlog_no)))
        row = ManualRestore(
            source=source, new_name=new_name, port=port, token_hash=_hash(token), full_backup_id=full.id, binlog=binlog,
            created_by=actor, expires_at=db_now_plus(hours * 3600),
            max_expires_at=db_now_plus(settings.MANUAL_RESTORE_MAX_HOURS * 3600), status="Aktif")
        db.add(row)
        await db.flush()
        for backup_id in ids:
            db.add(ManualRestoreFile(restore_id=row.id, backup_id=backup_id))
        db.add(ControlLog(container=source, actor=actor, action="Buat sesi Restore manual",
                          detail=f"menjadi {new_name}, port {port}, {hours} jam, full {full.relpath}"))
        await db.flush()
        await db.refresh(row)
    return row, token


def promote_binlog_start(name: str | None) -> int | None:
    from app.runners.standby import binlog_start_number
    return binlog_start_number(name)


async def extend(db: AsyncSession, restore_id: int, actor: str) -> None:
    async with db.begin():
        row = await db.scalar(select(ManualRestore).where(ManualRestore.id == restore_id).with_for_update())
        if row is None:
            raise RestoreError(404, "Sesi tidak ditemukan.")
        if row.status not in LIVE or row.expires_at <= await db.scalar(select(func.now())):
            raise RestoreError(409, "Sesi sudah tidak aktif.")
        if row.expires_at >= row.max_expires_at:
            raise RestoreError(409, f"Sesi sudah mencapai batas {settings.MANUAL_RESTORE_MAX_HOURS} jam.")
        row.expires_at = func.least(db_time_plus(ManualRestore.expires_at, EXTEND_SECONDS), ManualRestore.max_expires_at)
        db.add(ControlLog(container=row.source, actor=actor, action="Perpanjang sesi Restore manual",
                          detail=f"{row.new_name}: +2 jam"))


async def revoke(db: AsyncSession, restore_id: int, actor: str) -> None:
    async with db.begin():
        row = await db.scalar(select(ManualRestore).where(ManualRestore.id == restore_id).with_for_update())
        if row is None:
            raise RestoreError(404, "Sesi tidak ditemukan.")
        if row.status not in LIVE:
            raise RestoreError(409, "Sesi sudah selesai atau dicabut.")
        row.status, row.finished_at = "Dicabut", func.now()
        db.add(ControlLog(container=row.source, actor=actor, action="Cabut sesi Restore manual", detail=row.new_name))


async def protected_backup_ids(db: AsyncSession) -> set[int]:
    """File sesi yang masih aktif dilindungi dari retensi."""
    return set(await db.scalars(
        select(ManualRestoreFile.backup_id).join(ManualRestore, ManualRestore.id == ManualRestoreFile.restore_id)
        .where(ManualRestore.status.in_(LIVE), ManualRestore.expires_at > func.now())))


# ---------- sisi script ----------
async def _session(db: AsyncSession, token: str | None, machine_id: str | None, ip: str, *, bind: bool = False,
                   lock: bool = False) -> ManualRestore:
    """Memeriksa token dan mesin. Dipakai di dalam transaksi pemanggil."""
    _check_not_blocked(ip)
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        _record_failure(ip)
        raise RestoreError(401, "Token restore tidak dikenal.")
    if not machine_id or not _MACHINE_ID.fullmatch(machine_id):
        raise RestoreError(422, "machine-id server ini tidak terbaca.")
    query = select(ManualRestore).where(ManualRestore.token_hash == _hash(token))
    row = await db.scalar(query.with_for_update() if lock or bind else query)
    if row is None:
        _record_failure(ip)
        raise RestoreError(401, "Token restore tidak dikenal.")
    if row.status == "Dicabut":
        raise RestoreError(401, "Sesi ini sudah dicabut.")
    if row.status == "Selesai":
        raise RestoreError(401, "Sesi ini sudah selesai; token tidak berlaku lagi.")
    if row.expires_at <= await db.scalar(select(func.now())):
        raise RestoreError(401, "Sesi ini sudah kedaluwarsa. Buat sesi baru di tab Restore manual.")
    if row.machine_id and row.machine_id != machine_id:
        raise RestoreError(403, "Sesi ini sudah dipakai di mesin lain. Buat sesi baru bila ingin pindah.")
    if bind and row.machine_id is None:
        row.machine_id, row.claimed_at = machine_id, func.now()
    return row


async def claim(db: AsyncSession, token: str | None, machine_id: str | None, ip: str) -> str:
    """Jawaban teks untuk script sh: pasangan KEY=value dan satu baris FILE per file."""
    async with db.begin():
        row = await _session(db, token, machine_id, ip, bind=True)
        nas = await nas_service.get_view(db)
        if nas.problem:
            raise RestoreError(503, "NAS bermasalah; coba lagi sebentar lagi.")
        files = list(await db.scalars(
            select(Backup).join(ManualRestoreFile, ManualRestoreFile.backup_id == Backup.id)
            .where(ManualRestoreFile.restore_id == row.id).order_by(Backup.kind.desc(), Backup.binlog_no, Backup.id)))
        info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == row.source))
        tag = str((info or {}).get("agent_version") or "")
        tag = tag if _IMAGE_TAG.fullmatch(tag) else settings.IMAGE_TAG_DEFAULT
        full = next(f for f in files if f.kind == "fullbackup")
        if row.status != "Berjalan":
            db.add(ControlLog(container=row.source, actor="script restore manual", action="Jalankan perintah restore",
                              detail=f"{row.new_name}, machine-id {machine_id}"))
        row.status, row.step, row.note = "Berjalan", "mulai", None
        lines = [f"NAME={row.new_name}", f"PORT={row.port}", f"IMAGE_TAG={tag}", f"BINLOG_START={full.binlog_start or ''}",
                 f"SOURCE={row.source}"]
        for f in files:
            lines.append(f"FILE {f.id} {f.kind} {f.size} {f.sha256} {f.binlog_no if f.binlog_no is not None else '-'}")
    return "\n".join(lines) + "\n"


async def download_target(db: AsyncSession, token: str | None, machine_id: str | None, ip: str, backup_id: int,
                          offset: int) -> tuple[str, str, int, str]:
    async with db.begin():
        row = await _session(db, token, machine_id, ip)
        nas = await nas_service.get_view(db)
        if nas.problem:
            raise RestoreError(503, "NAS bermasalah.")
        found = (await db.execute(
            select(Backup.container, Backup.stored_path, Backup.size, Backup.sha256)
            .join(ManualRestoreFile, ManualRestoreFile.backup_id == Backup.id)
            .where(ManualRestoreFile.restore_id == row.id, Backup.id == backup_id))).one_or_none()
        if found is None:
            raise RestoreError(403, "File bukan bagian dari sesi ini.")
        db.add(ManualRestoreDownload(restore_id=row.id, backup_id=backup_id, offset=offset, ip=ip[:45]))
    return found.container, found.stored_path, found.size, found.sha256


async def progress(db: AsyncSession, token: str | None, machine_id: str | None, ip: str, step: str, failed: bool) -> None:
    step = step.strip()[:500] or "—"
    async with db.begin():
        row = await _session(db, token, machine_id, ip, lock=True)
        if failed:
            row.status, row.note, row.step = "Gagal", step[:500], row.step
        else:
            row.status, row.step, row.note = "Berjalan", step[:200], None


@dataclass(frozen=True)
class Registered:
    token: str
    age_recipients: str


async def register(db: AsyncSession, token: str | None, machine_id: str | None, ip: str) -> Registered:
    """Menulis baris agen di agents.conf. Bila gagal, sesi tetap aktif dan database yang sudah hidup di server tujuan
    tidak dipulihkan ulang: script cukup dijalankan lagi."""
    async with db.begin():
        row = await _session(db, token, machine_id, ip, lock=True)
        if row.status != "Berjalan":
            raise RestoreError(409, "Pemulihan belum dilaporkan selesai oleh script.")
        new_name, source, restore_id, machine = row.new_name, row.source, row.id, row.machine_id
        taken = set(await db.scalars(select(Container.name)))
    agent_token = agents.add(new_name, extra_names=frozenset(taken))
    try:
        async with db.begin():
            row = await db.scalar(select(ManualRestore).where(ManualRestore.id == restore_id).with_for_update())
            db.add(Container(name=new_name, origin="manual", source_container=source))
            await db.flush()
            source_cfg = await db.get(ContainerSettings, source)
            runner = await promote.pick_runner_for_machine(db, machine)
            if runner is not None:
                db.add(ContainerSettings(
                    container=new_name, test_runner=runner, standby_runner=runner,
                    test_schedule=(source_cfg.test_schedule if source_cfg else None) or settings.RESTORE_TEST_DEFAULT,
                    test_full=source_cfg.test_full if source_cfg else True,
                    test_databases=source_cfg.test_databases if source_cfg else None,
                    standby_enabled=source_cfg.standby_enabled if source_cfg else False,
                    standby_full_schedule=source_cfg.standby_full_schedule if source_cfg else None,
                    standby_binlog_schedule=source_cfg.standby_binlog_schedule if source_cfg else None))
            db.add(BackupRequest(container=new_name, kind="full", requested_by=row.created_by, status="Menunggu",
                                 note="Full pertama setelah Restore manual"))
            db.add(ControlLog(container=new_name, actor=row.created_by, action="Dibuat dari Restore manual",
                              detail=f"dari {source}" + ("" if runner else "; belum ada runner di server lain")))
            row.status, row.step, row.note, row.finished_at = "Selesai", "terdaftar", None, func.now()
    except Exception:
        agents.remove_pending(new_name)       # tokennya belum pernah sampai ke script, jadi barisnya ditarik lagi
        raise
    return Registered(agent_token, settings.BACKUP_AGE_RECIPIENTS)
