import re
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.backup_control import cron
from app.backup_control.models import CRON_LENGTH, BackupRequest, BackupSchedule, ContainerTuning, ControlLog
from app.backup_control.schemas import HeartbeatIn, RequestStatusIn
from app.backups.models import Backup
from app.containers.models import Heartbeat
from app.containers.service import ensure_row
from app.core.config import settings
from app.core.database import db_now_plus
from app.nas import service as nas_service
from app.reports import ingest as metrics_ingest
from app.agents.dependencies import registry

KINDS = ("full", "dump", "binlog")
DATABASE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ACTIVE_STATUSES = ("Menunggu", "Berjalan", "Mengirim")
TERMINAL_STATUSES = ("Selesai", "Gagal", "Tidak selesai", "Sudah berjalan", "Dibatalkan", "Ditolak", "Kedaluwarsa")
DISK_MARGIN = 1.2
# Yang boleh dilaporkan agen dari tiap status. "Tidak selesai" boleh dikoreksi: LumbungDB menandainya saat heartbeat
# berhenti, padahal bisa saja hanya jaringan yang putus dan script tetap selesai.
_TRANSITIONS = {
    "Menunggu": {"Berjalan", "Gagal", "Sudah berjalan", "Tidak selesai"},
    "Berjalan": {"Mengirim", "Selesai", "Gagal", "Sudah berjalan", "Tidak selesai"},
    "Mengirim": {"Selesai", "Gagal", "Tidak selesai"},
    "Tidak selesai": {"Mengirim", "Selesai", "Gagal"},
}


class ControlError(Exception):
    def __init__(self, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def request_label(kind: str, database: str | None) -> str:
    if kind == "full":
        return "Full"
    if kind == "binlog":
        return "Arsip binlog"
    return f"Dump {database}" if database else "Dump semua database"


# ---------- heartbeat ----------
async def record_heartbeat(db: AsyncSession, container: str, data: HeartbeatIn, ip: str | None = None) -> dict:
    """Jawaban berisi jadwal bila versinya berubah dan permintaan backup yang menunggu."""
    info = {**data.model_dump(exclude={"server", "machine_id", "schedule", "metrics"}), "ip": ip}
    if data.tuning is not None:
        info["tuning"] = data.tuning.model_dump(exclude={"version", "error"})
    if data.metrics is not None:
        info["metrics"] = {"performance_schema": data.metrics.performance_schema, "digests": data.metrics.digests,
                           "indexes": data.metrics.indexes}
    async with db.begin():
        await ensure_row(db, container)
        stmt = insert(Heartbeat).values(
            container=container, server=data.server, machine_id=data.machine_id, at=func.now(), info=info,
        )
        await db.execute(stmt.on_duplicate_key_update(
            server=stmt.inserted.server, machine_id=stmt.inserted.machine_id, at=func.now(), info=stmt.inserted.info,
        ))
        schedule = await _sync_schedule(db, container, data)
        tuning = await _sync_tuning(db, container, data)
        await metrics_ingest.ingest(db, container, data.metrics, data.disk, await db.scalar(select(func.now())))
        pending = (await db.execute(
            select(BackupRequest.id, BackupRequest.kind, BackupRequest.database)
            .where(BackupRequest.container == container, BackupRequest.status == "Menunggu",
                   BackupRequest.created_at > db_now_plus(-settings.BACKUP_REQUEST_TTL_MINUTES * 60))
            .order_by(BackupRequest.id)
        )).all()
    reply: dict = {"requests": [{"id": r.id, "kind": r.kind, "database": r.database} for r in pending]}
    if schedule is not None:
        reply["schedule"] = schedule
    if tuning is not None:
        reply["tuning"] = tuning
    return reply


async def _sync_schedule(db: AsyncSession, container: str, data: HeartbeatIn) -> dict | None:
    reported = data.schedule
    row = await db.get(BackupSchedule, container, with_for_update=True)
    if reported is None:
        wanted_row = row
    elif row is None:
        # Baru terdaftar atau metadata hilang: jadwal di agen tidak ditimpa, laporannya menjadi nilai awal.
        db.add(BackupSchedule(
            container=container, requested_full=reported.full, requested_dump=reported.dump,
            requested_binlog=reported.binlog, requested_version=reported.version, applied_version=reported.version,
            effective_full=reported.full, effective_dump=reported.dump, effective_binlog=reported.binlog,
            source=reported.source, reported_at=func.now(),
        ))
        return None
    else:
        row.applied_version = reported.version
        row.effective_full, row.effective_dump, row.effective_binlog = reported.full, reported.dump, reported.binlog
        row.source = reported.source
        row.reported_at = func.now()
        if reported.version > row.requested_version:
            # Metadata lebih tua dari agen, mis. setelah restore database LumbungDB
            row.requested_version = reported.version
            row.requested_full, row.requested_dump = reported.full, reported.dump
            row.requested_binlog = reported.binlog
        error = reported.error if isinstance(reported.error, dict) else None
        if error and error.get("version") == row.requested_version:
            row.error_version = row.requested_version
            row.error_message = str(error.get("message", "jadwal ditolak agen"))[:300]
        elif reported.version == row.requested_version:
            row.error_version = row.error_message = None
        wanted_row = row
    if wanted_row is None or wanted_row.requested_version <= wanted_row.applied_version:
        return None
    if wanted_row.error_version == wanted_row.requested_version:
        return None      # sudah ditolak; tidak diulang sampai admin mengubahnya lagi
    return {
        "version": wanted_row.requested_version, "full": wanted_row.requested_full,
        "dump": wanted_row.requested_dump, "binlog": wanted_row.requested_binlog,
    }


TUNING_FIELDS = ("mode", "memory_mb", "max_connections", "disk")


def _requested_tuning(row: ContainerTuning) -> dict:
    return {"mode": row.requested_mode, "memory_mb": row.requested_memory_mb,
            "max_connections": row.requested_max_connections, "disk": row.requested_disk}


def _set_requested_tuning(row: ContainerTuning, values: dict) -> None:
    row.requested_mode, row.requested_memory_mb = values["mode"], values["memory_mb"]
    row.requested_max_connections, row.requested_disk = values["max_connections"], values["disk"]


async def _sync_tuning(db: AsyncSession, container: str, data: HeartbeatIn) -> dict | None:
    """Sama dengan jadwal. Agen versi lama tidak melaporkan tuning, jadi barisnya tidak pernah dibuat dan tidak ada
    yang dikirim."""
    reported = data.tuning
    row = await db.get(ContainerTuning, container, with_for_update=True)
    if reported is None:
        wanted_row = row
    elif row is None:
        values = {key: getattr(reported, key) for key in TUNING_FIELDS}
        if tuning_problems(values):
            return None
        row = ContainerTuning(container=container, requested_version=reported.version,
                              applied_version=reported.version, reported_at=func.now())
        _set_requested_tuning(row, values)
        db.add(row)
        return None
    else:
        row.applied_version = reported.version
        row.reported_at = func.now()
        if reported.version > row.requested_version:
            values = {key: getattr(reported, key) for key in TUNING_FIELDS}
            if not tuning_problems(values):
                row.requested_version = reported.version
                _set_requested_tuning(row, values)
        error = reported.error if isinstance(reported.error, dict) else None
        if error and error.get("version") == row.requested_version:
            row.error_version = row.requested_version
            row.error_message = str(error.get("message", "tuning ditolak agen"))[:300]
        elif reported.version == row.requested_version:
            row.error_version = row.error_message = None
        wanted_row = row
    if wanted_row is None or wanted_row.requested_version <= wanted_row.applied_version:
        return None
    if wanted_row.error_version == wanted_row.requested_version:
        return None
    return {"version": wanted_row.requested_version, **_requested_tuning(wanted_row)}


# ---------- tuning ----------
TUNING_MODES = ("auto", "off")
TUNING_DISKS = ("ssd", "hdd")
TUNING_MEMORY_MB = (512, 4 * 1024 * 1024)      # sama dengan tuning.py di image
TUNING_CONNECTIONS = (10, 10000)


def tuning_problems(values: dict, limit_mb: int | None = None) -> dict[str, str]:
    problems = {}
    if values.get("mode") not in TUNING_MODES:
        problems["mode"] = "Pilih otomatis atau mati."
    memory = values.get("memory_mb")
    low, high = TUNING_MEMORY_MB
    if not isinstance(memory, int) or (memory != 0 and not low <= memory <= high):
        problems["memory_mb"] = f"Kosongkan untuk memakai limit container, atau isi {low}–{high} MB."
    elif limit_mb and memory > limit_mb:
        problems["memory_mb"] = f"Melebihi limit memori container ({limit_mb} MB). Naikkan DB_MEM_LIMIT di server dulu."
    connections = values.get("max_connections")
    low, high = TUNING_CONNECTIONS
    if not isinstance(connections, int) or not low <= connections <= high:
        problems["max_connections"] = f"Isi {low}–{high}."
    if values.get("disk") not in TUNING_DISKS:
        problems["disk"] = "Pilih SSD atau HDD."
    return problems


def _tuning_label(key: str, value) -> str:
    if key == "memory_mb":
        return "dari limit container" if not value else f"{value} MB"
    if key == "mode":
        return "otomatis" if value == "auto" else "mati"
    return str(value).upper() if key == "disk" else str(value)


async def get_tuning(db: AsyncSession, container: str) -> ContainerTuning | None:
    return await db.get(ContainerTuning, container)


async def update_tuning(db: AsyncSession, container: str, values: dict, actor: str, limit_mb: int | None = None) -> bool:
    """False bila tidak ada perubahan. Tiap perubahan menaikkan versi dan masuk control_log."""
    if tuning_problems(values, limit_mb):
        raise ControlError("Tuning tidak valid.", 422)
    if container not in registry.entries().active:
        raise ControlError("Container nonaktif.")
    async with db.begin():
        row = await db.get(ContainerTuning, container, with_for_update=True)
        if row is None:
            raise ControlError("Tuning bisa diubah setelah agen yang mendukungnya mengirim heartbeat.")
        old = _requested_tuning(row)
        titles = {"mode": "Mode", "memory_mb": "Memori", "max_connections": "Max connections", "disk": "Disk"}
        changes = [f"{titles[key]}: {_tuning_label(key, old[key])} → {_tuning_label(key, values[key])}"
                   for key in TUNING_FIELDS if old[key] != values[key]]
        if not changes:
            return False
        _set_requested_tuning(row, values)
        row.requested_version += 1
        row.error_version = row.error_message = None
        db.add(ControlLog(container=container, actor=actor, action="Ubah tuning", detail=" · ".join(changes)[:1000]))
    return True


# ---------- jadwal ----------
def schedule_problems(values: dict[str, str]) -> dict[str, str]:
    return {
        kind: "Bukan cron 5 kolom yang valid, atau tidak pernah berjalan."
        for kind in KINDS
        if len(values.get(kind, "")) > CRON_LENGTH or not cron.is_valid(values.get(kind, ""))
    }


async def update_schedule(db: AsyncSession, container: str, values: dict[str, str], actor: str) -> bool:
    """False bila tidak ada perubahan. Tiap perubahan menaikkan versi dan masuk control_log."""
    if schedule_problems(values):
        raise ControlError("Jadwal tidak valid.", 422)
    if container not in registry.entries().active:
        raise ControlError("Container nonaktif.")
    async with db.begin():
        row = await db.get(BackupSchedule, container, with_for_update=True)
        if row is None:
            raise ControlError("Jadwal bisa diubah setelah heartbeat pertama.")
        old = {"full": row.requested_full, "dump": row.requested_dump, "binlog": row.requested_binlog}
        changes = [
            f"{title}: {cron.label(old[kind])} → {cron.label(values[kind])}"
            for kind, title in (("full", "Full"), ("dump", "Dump"), ("binlog", "Binlog"))
            if old[kind] != values[kind]
        ]
        if not changes:
            return False
        row.requested_full, row.requested_dump = values["full"], values["dump"]
        row.requested_binlog = values["binlog"]
        row.requested_version += 1
        row.error_version = row.error_message = None
        db.add(ControlLog(container=container, actor=actor, action="Ubah jadwal", detail=" · ".join(changes)[:1000]))
    return True


# ---------- permintaan ----------
@dataclass(frozen=True)
class LastFull:
    size: int | None


async def create_request(
    db: AsyncSession, container: str, kind: str, database: str | None, actor: str,
) -> BackupRequest:
    if kind not in KINDS:
        raise ControlError("Jenis backup tidak dikenal.", 422)
    if database and (kind != "dump" or not DATABASE.fullmatch(database)):
        raise ControlError("Nama database tidak valid.", 422)
    if container not in registry.entries().active:
        raise ControlError("Container nonaktif.")
    async with db.begin():
        nas = await nas_service.get_view(db)
        if not nas.writable:
            raise ControlError("NAS bermasalah, hasil backup tidak bisa sampai ke NAS.", 503)
        active = await db.scalar(select(BackupRequest.id).where(
            BackupRequest.container == container, BackupRequest.kind == kind,
            BackupRequest.status.in_(ACTIVE_STATUSES),
        ).limit(1).with_for_update())
        if active:
            raise ControlError("Masih ada permintaan aktif untuk jenis ini.")
        status, note = "Menunggu", f"Kedaluwarsa dalam {settings.BACKUP_REQUEST_TTL_MINUTES} menit bila agen tidak mengambilnya"
        if kind == "full":
            status, note = await _disk_check(db, container)
        request = BackupRequest(
            container=container, kind=kind, database=database, requested_by=actor, status=status, note=note,
            finished_at=func.now() if status == "Ditolak" else None,
        )
        db.add(request)
        db.add(ControlLog(container=container, actor=actor, action="Minta backup",
                          detail=request_label(kind, database)))
        await db.flush()
        await db.refresh(request)
    return request


async def _disk_check(db: AsyncSession, container: str) -> tuple[str, str | None]:
    last_size = await db.scalar(
        select(Backup.size).where(Backup.container == container, Backup.kind == "fullbackup")
        .order_by(Backup.backup_at.desc()).limit(1)
    )
    info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == container))
    free = ((info or {}).get("disk") or {}).get("free")
    if last_size and isinstance(free, int) and free < last_size * DISK_MARGIN:
        return "Ditolak", "Sisa disk /backup kurang dari 1,2× ukuran full terakhir"
    return "Menunggu", f"Kedaluwarsa dalam {settings.BACKUP_REQUEST_TTL_MINUTES} menit bila agen tidak mengambilnya"


async def cancel_request(db: AsyncSession, container: str, request_id: int, actor: str) -> bool:
    """Hanya yang belum diambil agen. False bila tidak ada."""
    async with db.begin():
        row = await db.get(BackupRequest, request_id, with_for_update=True)
        if row is None or row.container != container:
            return False
        if row.status != "Menunggu":
            raise ControlError("Permintaan sudah diambil agen.")
        row.status, row.finished_at, row.note = "Dibatalkan", func.now(), "Dibatalkan sebelum diambil agen"
        db.add(ControlLog(container=container, actor=actor, action="Batalkan permintaan",
                          detail=request_label(row.kind, row.database)))
    return True


async def report_status(db: AsyncSession, container: str, request_id: int, data: RequestStatusIn) -> None:
    """Token hanya bisa mengubah permintaan milik container-nya."""
    async with db.begin():
        row = await db.get(BackupRequest, request_id, with_for_update=True)
        if row is None or row.container != container:
            raise ControlError("Permintaan tidak ditemukan.", 404)
        if data.status == row.status:
            return
        if data.status not in _TRANSITIONS.get(row.status, ()):
            raise ControlError(f"Status {row.status} tidak bisa menjadi {data.status}.")
        row.status = data.status
        if data.note:
            row.note = data.note
        if data.log:
            row.log = data.log
        if data.status == "Berjalan":
            row.started_at = func.now()
        if data.status in TERMINAL_STATUSES:
            row.finished_at = func.now()


async def expire_requests(db: AsyncSession) -> None:
    """Permintaan yang tidak diambil tidak boleh tiba-tiba berjalan di jam sibuk beberapa jam kemudian. Permintaan yang
    berjalan tetapi heartbeat-nya berhenti tidak diulang."""
    async with db.begin():
        await db.execute(
            update(BackupRequest)
            .where(BackupRequest.status == "Menunggu",
                   BackupRequest.created_at <= db_now_plus(-settings.BACKUP_REQUEST_TTL_MINUTES * 60))
            .values(status="Kedaluwarsa", finished_at=func.now(),
                    note=f"Agen tidak mengambilnya dalam {settings.BACKUP_REQUEST_TTL_MINUTES} menit")
        )
        await db.execute(
            update(BackupRequest)
            .where(BackupRequest.status.in_(("Berjalan", "Mengirim")),
                   BackupRequest.container.in_(
                       select(Heartbeat.container)
                       .where(Heartbeat.at <= db_now_plus(-settings.ALERT_HEARTBEAT_MINUTES * 60))))
            .values(status="Tidak selesai", finished_at=func.now(), note="Agen berhenti saat berjalan; tidak diulang")
        )


async def active_kinds(db: AsyncSession, container: str) -> set[str]:
    return set(await db.scalars(
        select(BackupRequest.kind).where(BackupRequest.container == container,
                                         BackupRequest.status.in_(ACTIVE_STATUSES))
    ))


async def disable_container(db: AsyncSession, container: str, actor: str) -> None:
    """Memberi # di baris agen, lalu mencatat. File di NAS dan riwayat tidak disentuh."""
    if not registry.disable(container):
        raise ControlError("Agen tidak aktif di agents.conf.")
    async with db.begin():
        db.add(ControlLog(container=container, actor=actor, action="Nonaktifkan", detail="Baris agen dikomentari"))


async def requests_for(db: AsyncSession, container: str, limit: int = 20) -> list[BackupRequest]:
    return list(await db.scalars(
        select(BackupRequest).where(BackupRequest.container == container)
        .order_by(BackupRequest.id.desc()).limit(limit)
    ))


async def control_log_for(db: AsyncSession, container: str, limit: int = 50) -> list[ControlLog]:
    return list(await db.scalars(
        select(ControlLog).where(ControlLog.container == container).order_by(ControlLog.id.desc()).limit(limit)
    ))


async def last_full_duration(db: AsyncSession, container: str) -> tuple[int | None, float | None]:
    """Ukuran full terakhir dan durasi permintaan full terakhir yang selesai, untuk dialog sebelum full."""
    size = await db.scalar(
        select(Backup.size).where(Backup.container == container, Backup.kind == "fullbackup")
        .order_by(Backup.backup_at.desc()).limit(1)
    )
    row = (await db.execute(
        select(BackupRequest.started_at, BackupRequest.finished_at)
        .where(BackupRequest.container == container, BackupRequest.kind == "full", BackupRequest.status == "Selesai")
        .order_by(BackupRequest.id.desc()).limit(1)
    )).one_or_none()
    minutes = None
    if row and row.started_at and row.finished_at:
        minutes = (row.finished_at - row.started_at).total_seconds() / 60
    return size, minutes
