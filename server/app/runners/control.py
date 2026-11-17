"""Container yang dikelola runner: Tambah container langsung di mesin runner, lalu start/stop/restart dari dashboard.

Server hanya membuat tugas dan mencatat hasilnya. Password database dibuat acak oleh runner dan disimpan di mesin
runner (`<SCRATCH_DIR>/containers/<nama>/.env`); tidak pernah dikirim ke LumbungDB.
"""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.agents_file import project_name
from app.agents.dependencies import registry as agents
from app.backup_control.models import ControlLog
from app.core.config import settings
from app.runners import service
from app.runners.dependencies import registry as runners
from app.containers.models import Heartbeat
from app.runners.models import ContainerUpdate, Job, ManagedContainer, RunnerHeartbeat
from app.runners.schemas import JobResultIn

KIND_INSTALL = "container_install"
ACTIONS = {"start": "container_start", "stop": "container_stop", "restart": "container_restart"}
KIND_UPDATE = "container_update"
ACTION_LABEL = {"container_install": "Pasang", "container_start": "Start", "container_stop": "Stop",
                "container_restart": "Restart", "container_update": "Update image"}
TAG = re.compile(r"^(\d{1,3})\.(\d{1,3})-a(\d{1,4})$")      # versi MariaDB lalu versi agen, mis. 12.3-a7
DEFAULT_CPUS = "2"
DEFAULT_MEMORY = "2g"


def database_name(name: str) -> str:
    """Sama dengan script pasang: radiologi-db → radiologi."""
    return re.sub(r"[^A-Za-z0-9_]", "_", re.sub(r"[-_.]db$", "", name, flags=re.IGNORECASE))


def image(tag: str | None = None) -> str:
    return f"zmutclik/mariadb:lumbungdb-{tag or settings.IMAGE_TAG_DEFAULT}"


async def get(db: AsyncSession, container: str) -> ManagedContainer | None:
    return await db.get(ManagedContainer, container)


async def has_active_job(db: AsyncSession, container: str) -> bool:
    return bool(await db.scalar(select(Job.id).where(
        Job.container == container, Job.kind.in_(service.CONTROL_KINDS), Job.status.in_(service.UNFINISHED)).limit(1)))


def install_job(container: str, runner: str, actor: str) -> Job:
    return Job(runner=runner, container=container, kind=KIND_INSTALL, status=service.STATUS_WAITING, requested_by=actor)


async def create_install(db: AsyncSession, container: str, runner: str, port: int, actor: str) -> Job:
    """Dipanggil sesudah nama terdaftar di agents.conf. Kegagalan di sini diteruskan; pemanggil menarik pendaftarannya."""
    if runner not in runners.entries().active:
        raise service.RunnerError(422, "Pilih runner yang aktif.")
    async with db.begin():
        db.add(ManagedContainer(container=container, runner=runner, port=port, origin="install", created_by=actor))
        job = install_job(container, runner, actor)
        db.add(job)
        db.add(ControlLog(container=container, actor=actor, action="Pasang lewat runner",
                          detail=f"runner {runner}, port {port}"))
    return job


async def retry_install(db: AsyncSession, container: str, actor: str) -> Job:
    """Pasang ulang setelah gagal, selama agen belum pernah terhubung."""
    from app.containers import service as container_service
    async with db.begin():
        row = await db.get(ManagedContainer, container, with_for_update=True)
        if row is None or row.origin != "install":
            raise service.RunnerError(404, "Container ini tidak dipasang lewat runner.")
        if await container_service.has_heartbeat(db, container):
            raise service.RunnerError(409, "Agen sudah terhubung; pemasangan tidak perlu diulang.")
        if await has_active_job(db, container):
            raise service.RunnerError(409, "Masih ada tugas yang berjalan atau menunggu untuk container ini.")
        if row.runner not in runners.entries().active:
            raise service.RunnerError(409, f"Runner {row.runner} nonaktif.")
        job = install_job(container, row.runner, actor)
        db.add(job)
        db.add(ControlLog(container=container, actor=actor, action="Pasang ulang lewat runner", detail=f"runner {row.runner}"))
    return job


async def create_power(db: AsyncSession, container: str, action: str, actor: str) -> Job:
    if action not in ACTIONS:
        raise service.RunnerError(422, "Aksi tidak dikenal.")
    async with db.begin():
        row = await db.get(ManagedContainer, container)
        if row is None:
            raise service.RunnerError(404, "Container ini tidak dipasang lewat runner, jadi tidak bisa dikendalikan dari sini.")
        if row.runner not in runners.entries().active:
            raise service.RunnerError(409, f"Runner {row.runner} nonaktif.")
        if await has_active_job(db, container):
            raise service.RunnerError(409, "Masih ada aksi yang berjalan atau menunggu untuk container ini.")
        job = Job(runner=row.runner, container=container, kind=ACTIONS[action], status=service.STATUS_WAITING,
                  requested_by=actor)
        db.add(job)
        db.add(ControlLog(container=container, actor=actor, action=f"{ACTION_LABEL[ACTIONS[action]]} container",
                          detail=f"lewat runner {row.runner}"))
    return job


async def current_tag(db: AsyncSession, container: str) -> str | None:
    """Tag image yang sedang berjalan: dari agen (versi image), atau dari image yang dilaporkan runner."""
    info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == container))
    reported = (info or {}).get("agent_version")
    if reported and TAG.fullmatch(reported):
        return reported
    state = await live_state(db, await db.get(ManagedContainer, container))
    image_name = (state or {}).get("image", "")
    tag = image_name.rsplit(":lumbungdb-", 1)[-1] if ":lumbungdb-" in image_name else None
    return tag if tag and TAG.fullmatch(tag) else None


def suggested_tag(current: str | None) -> str:
    """Versi MariaDB yang sama dengan agen terbaru; tanpa versi sekarang, tag bawaan."""
    latest = TAG.fullmatch(settings.IMAGE_TAG_DEFAULT)
    found = TAG.fullmatch(current or "")
    if found and latest:
        return f"{found[1]}.{found[2]}-a{max(int(found[3]), int(latest[3]))}"
    return settings.IMAGE_TAG_DEFAULT


async def create_update(db: AsyncSession, container: str, tag: str, actor: str) -> Job:
    """Update image. Versi MariaDB tidak boleh turun: datadir yang sudah di-upgrade tidak terbaca versi lama."""
    found = TAG.fullmatch(tag or "")
    if not found:
        raise service.RunnerError(422, "Tag harus berbentuk versi MariaDB lalu versi agen, mis. 12.3-a7.")
    async with db.begin():
        current = await current_tag(db, container)
        if current:
            now = TAG.fullmatch(current)
            if (int(found[1]), int(found[2])) < (int(now[1]), int(now[2])):
                raise service.RunnerError(422, f"Versi MariaDB tidak boleh turun dari {now[1]}.{now[2]}.")
            if tag == current:
                raise service.RunnerError(422, f"{container} sudah memakai {tag}.")
        row = await db.get(ManagedContainer, container)
        if row is None:
            raise service.RunnerError(404, "Container ini tidak dipasang lewat runner, jadi tidak bisa diupdate dari sini.")
        if row.runner not in runners.entries().active:
            raise service.RunnerError(409, f"Runner {row.runner} nonaktif.")
        if await has_active_job(db, container):
            raise service.RunnerError(409, "Masih ada aksi yang berjalan atau menunggu untuk container ini.")
        job = Job(runner=row.runner, container=container, kind=KIND_UPDATE, status=service.STATUS_WAITING,
                  requested_by=actor)
        db.add(job)
        await db.flush()
        db.add(ContainerUpdate(job_id=job.id, image_tag=tag))
        db.add(ControlLog(container=container, actor=actor, action="Update image",
                          detail=f"{current or 'versi tidak terbaca'} → {tag}, lewat runner {row.runner}"))
    return job


async def claim_context(db: AsyncSession, job: Job) -> dict:
    """Tambahan jawaban klaim. Token agen dibaca dari agents.conf, bukan dari database."""
    if job.kind == KIND_UPDATE:
        target = await db.get(ContainerUpdate, job.id)
        return {"update": {"image": image(target.image_tag)}} if target else {}
    if job.kind != KIND_INSTALL:
        return {}
    row = await db.get(ManagedContainer, job.container)
    token = agents.entries().active.get(job.container)
    if row is None or token is None:
        return {}
    return {"install": {"name": job.container, "project": project_name(job.container), "port": row.port, "token": token,
            "age_recipients": settings.BACKUP_AGE_RECIPIENTS, "image": image(), "db_name": database_name(job.container),
            "cpus": DEFAULT_CPUS, "memory": DEFAULT_MEMORY}}


async def record_result(db: AsyncSession, job: Job, data: JobResultIn) -> None:
    """Dipanggil dari finish_job di dalam transaksinya."""
    if job.kind not in service.CONTROL_KINDS:
        return
    detail = f"{data.status}" + (f": {data.note}" if data.note else "")
    db.add(ControlLog(container=job.container, actor=f"runner {job.runner}",
                      action=f"{ACTION_LABEL[job.kind]} container selesai", detail=detail[:500]))


async def latest_job(db: AsyncSession, container: str) -> Job | None:
    return await db.scalar(select(Job).where(Job.container == container, Job.kind.in_(service.CONTROL_KINDS))
                           .order_by(Job.id.desc()).limit(1))


async def live_state(db: AsyncSession, row: ManagedContainer | None) -> dict | None:
    """Status container menurut heartbeat runner terakhir: {state, status} atau None bila belum terbaca."""
    if row is None:
        return None
    reported = await db.scalar(select(RunnerHeartbeat.containers).where(RunnerHeartbeat.runner == row.runner))
    return next((c for c in reported or () if c.get("name") == row.container), None)
