"""Database cadangan: tugas pemulihan full + binlog dan penerapan binlog baru, serta pencatatan hasilnya."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backups.models import Backup
from app.runners import service
from app.runners.models import ContainerSettings, Job, JobFile, RestoreTest, Standby, StandbyRun
from app.runners.schemas import JobResultIn

KIND_FULL = "standby_full"
KIND_APPLY = "standby_apply"
KIND_REMOVE = "standby_remove"
KINDS = (KIND_FULL, KIND_APPLY)
ALL_KINDS = (*KINDS, KIND_REMOVE)


def binlog_start_number(name: str | None) -> int | None:
    digits = (name or "").rsplit(".", 1)[-1]
    return int(digits) if digits.isdigit() else None


async def has_active_job(db: AsyncSession, container: str) -> bool:
    return bool(await db.scalar(select(Job.id).where(
        Job.container == container, Job.kind.in_(KINDS), Job.status.in_(service.UNFINISHED)).limit(1)))


async def create_full_job(db: AsyncSession, container: str, runner: str, *, requested_by: str | None = None) -> Job:
    """Full terbaru yang tidak pernah gagal uji, beserta binlog serantai mulai dari binlog_start-nya."""
    async with db.begin():
        if await has_active_job(db, container):
            raise service.RunnerError(409, "Masih ada restore yang berjalan atau menunggu untuk container ini.")
        failed = select(RestoreTest.backup_id).where(RestoreTest.status == "Gagal", RestoreTest.backup_id.is_not(None))
        full = (await db.execute(
            select(Backup).where(Backup.container == container, Backup.kind == "fullbackup", Backup.id.not_in(failed))
            .order_by(Backup.backup_at.desc(), Backup.id.desc()).limit(1))).scalar_one_or_none()
        if full is None:
            raise service.RunnerError(409, "Belum ada full yang bisa dipulihkan.")
        ids = [full.id]
        start = binlog_start_number(full.binlog_start)
        if full.chain_id is not None and start is not None:
            ids += list(await db.scalars(
                select(Backup.id).where(Backup.container == container, Backup.kind == "binlog",
                                        Backup.chain_id == full.chain_id, Backup.binlog_no >= start)
                .order_by(Backup.binlog_no)))
        job = Job(runner=runner, container=container, kind=KIND_FULL, status=service.STATUS_WAITING,
                  requested_by=requested_by)
        db.add(job)
        await db.flush()
        for backup_id in ids:
            db.add(JobFile(job_id=job.id, backup_id=backup_id))
    return job


def remove_job(container: str, runner: str) -> Job:
    """Menghapus database cadangan di satu runner. Tanpa file: runner hanya membersihkan mesinnya sendiri."""
    return Job(runner=runner, container=container, kind=KIND_REMOVE, status=service.STATUS_WAITING)


async def create_apply_job(db: AsyncSession, container: str, runner: str) -> Job | None:
    """None bila tidak ada binlog baru, atau database cadangan belum ada, atau rantainya berganti."""
    async with db.begin():
        if await has_active_job(db, container):
            return None
        standby = await db.get(Standby, container)
        if standby is None or standby.chain_id is None or standby.applied_binlog_no is None or standby.runner != runner:
            return None
        ids = list(await db.scalars(
            select(Backup.id).where(Backup.container == container, Backup.kind == "binlog",
                                    Backup.chain_id == standby.chain_id, Backup.binlog_no > standby.applied_binlog_no)
            .order_by(Backup.binlog_no)))
        if not ids:
            return None
        job = Job(runner=runner, container=container, kind=KIND_APPLY, status=service.STATUS_WAITING)
        db.add(job)
        await db.flush()
        for backup_id in ids:
            db.add(JobFile(job_id=job.id, backup_id=backup_id))
    return job


async def claim_context(db: AsyncSession, job: Job) -> dict | None:
    """Keadaan database cadangan yang dibutuhkan runner untuk tugas penerapan."""
    if job.kind not in ALL_KINDS:
        return None
    standby = await db.get(Standby, job.container)
    return {"applied_binlog_no": standby.applied_binlog_no if standby else None,
            "port": standby.port if standby else None}


async def record_result(db: AsyncSession, job: Job, data: JobResultIn, allowed: set[int]) -> None:
    """Dipanggil dari finish_job di dalam transaksinya. Hanya hasil Lolos mengubah keadaan database cadangan."""
    if job.kind not in ALL_KINDS:
        return
    kind_label = {KIND_FULL: "full", KIND_APPLY: "binlog", KIND_REMOVE: "hapus"}[job.kind]
    db.add(StandbyRun(container=job.container, job_id=job.id, runner=job.runner, kind=kind_label,
                      manual=job.requested_by is not None, status=data.status, note=data.note))
    if job.kind == KIND_REMOVE:
        if data.status == "Lolos":
            row = await db.get(Standby, job.container, with_for_update=True)
            if row is not None and row.runner == job.runner:
                await db.delete(row)         # salinan di runner yang masih dipakai saja yang menghapus barisnya
        return
    info = data.standby
    if job.kind == KIND_APPLY and data.status == "Gagal":
        # Sebagian binlog mungkin sudah masuk. Menerapkannya lagi memberi data ganda, jadi penerapan berhenti sampai
        # pemulihan full berikutnya.
        row = await db.get(Standby, job.container, with_for_update=True)
        if row is not None:
            row.applied_binlog_no = None
            row.updated_at = func.now()
        return
    if data.status != "Lolos" or info is None:
        return
    row = await db.get(Standby, job.container, with_for_update=True)
    if row is None:
        row = Standby(container=job.container)
        db.add(row)
    if job.kind == KIND_FULL:
        if info.full_backup_id is None or info.full_backup_id not in allowed:
            raise service.RunnerError(403, "Full bukan bagian dari tugas ini.")
        full = await db.get(Backup, info.full_backup_id)
        if row.runner and row.runner != job.runner:
            # Pindah runner: salinan lama dihapus hanya sesudah yang baru lolos.
            db.add(remove_job(job.container, row.runner))
        row.full_backup_id, row.chain_id, row.runner = full.id, full.chain_id, job.runner
        row.restored_at = func.now()
        row.data_until = full.backup_at
        row.applied_binlog_no = info.applied_binlog_no if info.applied_binlog_no is not None else \
            (binlog_start_number(full.binlog_start) - 1 if binlog_start_number(full.binlog_start) is not None else None)
    elif info.applied_binlog_no is not None:
        row.applied_binlog_no = info.applied_binlog_no
    if job.kind == KIND_FULL and job.requested_by is not None:
        # Restore sekarang di runner lain: setelah lolos, runner itu menjadi runner cadangan.
        settings_row = await db.get(ContainerSettings, job.container, with_for_update=True)
        if settings_row is not None:
            settings_row.standby_runner = job.runner
    if info.port:
        row.port = info.port
    if info.applied_binlog_no is not None and row.chain_id is not None:
        newest = await db.scalar(select(Backup.backup_at).where(
            Backup.container == job.container, Backup.kind == "binlog", Backup.chain_id == row.chain_id,
            Backup.binlog_no == info.applied_binlog_no, Backup.id.in_(allowed)))
        if newest is not None:
            row.data_until = newest
    row.updated_at = func.now()
