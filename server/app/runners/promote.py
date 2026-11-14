"""Naikkan ke production: database cadangan di mesin runner menjadi container production dengan nama baru.

Server hanya menyiapkan tugas dan mencatat hasil. Pembuatan user backup, penghapusan akun runner, dan penjalanan
container dikerjakan runner di mesinnya sendiri (lihat runner/runner.py, StandbyRunner.promote).
"""
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry as agents
from app.backup_control.models import BackupRequest, ControlLog
from app.containers import service as container_service
from app.core.config import settings
from app.runners import service, standby
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, Promotion, RunnerHeartbeat, Standby

logger = logging.getLogger(__name__)

MIN_PORT = 1024


async def create(db: AsyncSession, source: str, new_name: str, port: int, actor: str) -> Job:
    """Mendaftarkan nama baru di agents.conf, lalu membuat tugas untuk runner yang memegang database cadangan.

    NameRejected dan AgentsFileWriteError dari pendaftaran diteruskan ke pemanggil; pada kedua kasus tidak ada yang
    berubah. Bila pembuatan tugas gagal sesudah pendaftaran, baris agen ditarik lagi karena tokennya belum pernah
    dipakai."""
    if not MIN_PORT <= port <= 65535:
        raise service.RunnerError(422, f"Port harus antara {MIN_PORT} dan 65535.")
    async with db.begin():
        state = await db.get(Standby, source)
        if state is None or not state.runner or state.full_backup_id is None:
            raise service.RunnerError(409, "Belum ada database cadangan yang siap dinaikkan.")
        if state.applied_binlog_no is None and state.chain_id is not None:
            raise service.RunnerError(
                409, "Penerapan binlog database cadangan terhenti; jalankan Restore sekarang dulu agar datanya utuh.")
        if state.runner not in runners.entries().active:
            raise service.RunnerError(409, f"Runner {state.runner} nonaktif; database cadangan tidak bisa dijalankan.")
        runner = state.runner
        if await standby.has_active_job(db, source):
            raise service.RunnerError(409, "Masih ada restore atau penaikan yang berjalan untuk container ini.")
    await container_service.register(db, new_name, origin="promote", source=source)
    try:
        async with db.begin():
            if await standby.has_active_job(db, source):
                raise service.RunnerError(409, "Masih ada restore atau penaikan yang berjalan untuk container ini.")
            job = Job(runner=runner, container=source, kind=standby.KIND_PROMOTE, status=service.STATUS_WAITING,
                      requested_by=actor)
            db.add(job)
            await db.flush()
            db.add(Promotion(job_id=job.id, new_name=new_name, port=port, requested_by=actor))
            db.add(ControlLog(container=source, actor=actor, action="Naikkan ke production",
                              detail=f"menjadi {new_name} di runner {runner}, port {port}"))
            db.add(ControlLog(container=new_name, actor=actor, action="Dibuat dari Naikkan ke production",
                              detail=f"dari database cadangan {source}"))
    except Exception:
        await container_service.cancel_pending(db, new_name)
        raise
    return job


async def claim_context(db: AsyncSession, job: Job) -> dict | None:
    """Yang dibutuhkan runner untuk membuat container baru. Token agen dibaca dari agents.conf, bukan dari database."""
    promotion = await db.get(Promotion, job.id)
    if promotion is None:
        return None
    token = agents.entries().active.get(promotion.new_name)
    if token is None:
        return None
    return {"name": promotion.new_name, "port": promotion.port, "token": token,
            "age_recipients": settings.BACKUP_AGE_RECIPIENTS}


async def pick_runner(db: AsyncSession, current: str) -> str | None:
    """Runner untuk container baru; yang di server lain dari runner pemegangnya didahulukan."""
    here = await db.scalar(select(RunnerHeartbeat.machine_id).where(RunnerHeartbeat.runner == current))
    return await pick_runner_for_machine(db, here, exclude=current)


async def pick_runner_for_machine(db: AsyncSession, machine_id: str | None, exclude: str | None = None) -> str | None:
    """Runner untuk container di mesin `machine_id`. Urutan: sehat di server lain, bermasalah di server lain, sehat di
    server yang sama, lalu sisanya. Runner bermasalah tetap ditetapkan supaya alert runner menjelaskan dampaknya.
    `exclude` (runner pemegang) hanya dipakai bila tidak ada runner lain."""
    beats = {r.runner: r for r in await db.scalars(select(RunnerHeartbeat))}
    now = await db.scalar(select(func.now()))
    limit = settings.ALERT_HEARTBEAT_MINUTES * 60

    def rank(name: str) -> tuple:
        beat = beats.get(name)
        same = beat is None or not machine_id or beat.machine_id == machine_id
        healthy = beat is not None and (now - beat.at).total_seconds() <= limit
        return (name == exclude, same, not healthy, name)

    names = sorted(runners.entries().active, key=rank)
    return names[0] if names else None


async def record_success(db: AsyncSession, job: Job) -> None:
    """Dipanggil di dalam transaksi hasil tugas. Container baru mendapat penetapan runner (server lain didahulukan) dan langsung
    meminta full pertama; database cadangan sumber hilang karena datanya sekarang production."""
    promotion = await db.get(Promotion, job.id)
    if promotion is None:
        raise service.RunnerError(404, "Penaikan tidak ditemukan.")
    new_name = promotion.new_name
    source_cfg = await db.get(ContainerSettings, job.container, with_for_update=True)
    runner = await pick_runner(db, job.runner)
    if runner is not None and await db.get(ContainerSettings, new_name) is None:
        db.add(ContainerSettings(
            container=new_name, test_runner=runner, standby_runner=runner,
            test_schedule=(source_cfg.test_schedule if source_cfg else None) or settings.RESTORE_TEST_DEFAULT,
            test_full=source_cfg.test_full if source_cfg else True,
            test_databases=source_cfg.test_databases if source_cfg else None,
            standby_enabled=source_cfg.standby_enabled if source_cfg else False,
            standby_full_schedule=source_cfg.standby_full_schedule if source_cfg else None,
            standby_binlog_schedule=source_cfg.standby_binlog_schedule if source_cfg else None))
    if source_cfg is not None:
        source_cfg.standby_enabled = False
    row = await db.get(Standby, job.container, with_for_update=True)
    if row is not None:
        await db.delete(row)
    db.add(BackupRequest(container=new_name, kind="full", requested_by=promotion.requested_by, status="Menunggu",
                         note="Full pertama setelah Naikkan ke production"))
    db.add(ControlLog(container=new_name, actor=promotion.requested_by, action="Naik ke production",
                      detail=f"berjalan di runner {job.runner}, port {promotion.port}"
                             + ("" if runner else "; belum ada runner")))


async def abandon(db: AsyncSession, job_id: int) -> None:
    """Penaikan gagal atau tidak terlaksana: pendaftaran nama baru ditarik selama agennya belum pernah terhubung."""
    async with db.begin():
        promotion = await db.get(Promotion, job_id)
        new_name = promotion.new_name if promotion else None
        connected = new_name is not None and await container_service.has_heartbeat(db, new_name)
        if new_name is None or connected:
            return
        await db.execute(delete(Promotion).where(Promotion.job_id == job_id))
    try:
        await container_service.cancel_pending(db, new_name)
    except container_service.AlreadyConnected:
        logger.warning("promote_abandon_connected", extra={"container": new_name})
