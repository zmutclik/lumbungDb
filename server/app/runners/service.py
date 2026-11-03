"""Tugas runner: siapa mengerjakan apa, dan file apa yang boleh ia unduh."""
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.backups.models import Backup
from app.containers.models import Heartbeat
from app.core.config import settings
from app.core.database import db_now_plus
from app.nas import service as nas_service
from app.runners.models import Job, JobDownload, JobFile, RestoreTest, RunnerHeartbeat
from app.runners.schemas import JobResultIn, RunnerHeartbeatIn

STATUS_WAITING = "Menunggu"
STATUS_RUNNING = "Berjalan"
STATUS_NOT_RUN = "Tidak terlaksana"
UNFINISHED = (STATUS_WAITING, STATUS_RUNNING)
USERS_DATABASE = "_users"


class RunnerError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class DownloadTarget:
    container: str
    stored_path: str
    size: int
    sha256: str


async def record_heartbeat(db: AsyncSession, runner: str, data: RunnerHeartbeatIn) -> None:
    values = data.model_dump()
    statement = insert(RunnerHeartbeat).values(runner=runner, at=func.now(), **values)
    async with db.begin():
        await db.execute(statement.on_duplicate_key_update(at=func.now(), **values))


def file_row(backup: Backup) -> dict:
    return {
        "id": backup.id, "kind": backup.kind, "database": backup.database, "relpath": backup.relpath,
        "size": backup.size, "sha256": backup.sha256, "plain_sha256": backup.plain_sha256,
        "binlog_no": backup.binlog_no, "chain_id": backup.chain_id, "binlog_start": backup.binlog_start,
    }


async def _same_server(db: AsyncSession, runner: str, container: str) -> bool:
    """Salinan tidak boleh satu server dengan sumbernya. machine-id, bukan nama, karena nama bisa sama atau berubah."""
    runner_machine = await db.scalar(select(RunnerHeartbeat.machine_id).where(RunnerHeartbeat.runner == runner))
    container_machine = await db.scalar(select(Heartbeat.machine_id).where(Heartbeat.container == container))
    return bool(runner_machine) and runner_machine == container_machine


async def claim_next(db: AsyncSession, runner: str) -> dict | None:
    """Tiap runner mengerjakan satu tugas. Batas total memakai penghitungan biasa: pelampauan sesaat hanya
    menambah satu unduhan, sedangkan batas unduhan tetap dijaga terpisah."""
    async with db.begin():
        nas = await nas_service.get_view(db)
        if nas.problem:
            return None
        running = await db.scalar(
            select(func.count()).select_from(Job).where(Job.runner == runner, Job.status == STATUS_RUNNING))
        total = await db.scalar(select(func.count()).select_from(Job).where(Job.status == STATUS_RUNNING))
        if running or total >= settings.RUNNER_MAX_TOTAL:
            return None
        job = await db.scalar(
            select(Job).where(Job.runner == runner, Job.status == STATUS_WAITING)
            .order_by(Job.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if await _same_server(db, runner, job.container):
            job.status = STATUS_NOT_RUN
            job.note = "Runner satu server dengan container ini"
            job.finished_at = func.now()
            return None
        job.status = STATUS_RUNNING
        job.claimed_at = func.now()
        job.deadline_at = db_now_plus(settings.JOB_TIMEOUT_MINUTES * 60)
        files = list(await db.scalars(
            select(Backup).join(JobFile, JobFile.backup_id == Backup.id).where(JobFile.job_id == job.id)
            .order_by(Backup.id)))
        reply = {"id": job.id, "kind": job.kind, "container": job.container, "files": [file_row(f) for f in files]}
        from app.runners import standby      # standby memakai modul ini juga
        if (context := await standby.claim_context(db, job)) is not None:
            reply["standby"] = context
        return reply


async def _own_running_job(db: AsyncSession, runner: str, job_id: int) -> Job:
    job = await db.scalar(select(Job).where(Job.id == job_id, Job.runner == runner).with_for_update())
    if job is None:
        raise RunnerError(404, "Tugas tidak ditemukan.")
    if job.status != STATUS_RUNNING:
        raise RunnerError(409, "Tugas tidak sedang berjalan.")
    return job


async def report_progress(db: AsyncSession, runner: str, job_id: int, step: str) -> None:
    async with db.begin():
        job = await _own_running_job(db, runner, job_id)
        job.step = step
        job.deadline_at = db_now_plus(settings.JOB_TIMEOUT_MINUTES * 60)


async def finish_job(db: AsyncSession, runner: str, job_id: int, data: JobResultIn) -> None:
    async with db.begin():
        job = await _own_running_job(db, runner, job_id)
        allowed = set(await db.scalars(select(JobFile.backup_id).where(JobFile.job_id == job_id)))
        for item in data.results:
            # Bukti uji menentukan apa yang dilindungi retensi, jadi hanya berlaku untuk file milik tugas ini.
            if item.backup_id is not None and item.backup_id not in allowed:
                raise RunnerError(403, "File bukan bagian dari tugas ini.")
            db.add(RestoreTest(job_id=job_id, container=job.container, runner=runner, **item.model_dump()))
        from app.runners import standby
        await standby.record_result(db, job, data, allowed)
        job.status = data.status
        job.note = data.note
        job.finished_at = func.now()


async def download_target(db: AsyncSession, runner: str, job_id: int, backup_id: int, offset: int, ip: str) -> DownloadTarget:
    """403 sama untuk tugas orang lain, tugas selesai, dan file di luar tugas: tidak membocorkan mana yang ada."""
    async with db.begin():
        nas = await nas_service.get_view(db)
        if nas.problem:
            raise RunnerError(503, "NAS bermasalah.")
        row = (await db.execute(
            select(Backup.container, Backup.stored_path, Backup.size, Backup.sha256)
            .join(JobFile, JobFile.backup_id == Backup.id)
            .join(Job, Job.id == JobFile.job_id)
            .where(Job.id == job_id, Job.runner == runner, Job.status == STATUS_RUNNING, Backup.id == backup_id)
        )).one_or_none()
        if row is None:
            raise RunnerError(403, "File bukan bagian dari tugas runner ini.")
        db.add(JobDownload(job_id=job_id, backup_id=backup_id, runner=runner, offset=offset, ip=ip[:45]))
    return DownloadTarget(row.container, row.stored_path, row.size, row.sha256)


async def expire_jobs(db: AsyncSession) -> None:
    """Runner mati di tengah tugas: tidak terlaksana, bukan gagal, karena backup-nya bisa saja sehat."""
    async with db.begin():
        await db.execute(
            update(Job).where(Job.status == STATUS_RUNNING, Job.deadline_at <= func.now())
            .values(status=STATUS_NOT_RUN, note="Batas waktu tugas habis", finished_at=func.now())
        )


async def create_restore_test(db: AsyncSession, container: str, runner: str, *, full: bool = True,
                              databases: list[str] | None = None, requested_by: str | None = None) -> Job:
    """File terbaru tiap jenis. Dump _users selalu ikut, dan binlog mengikuti rantai full yang dipilih."""
    async with db.begin():
        selected: list[int] = []
        chosen_full = None
        if full:
            chosen_full = (await db.execute(
                select(Backup.id, Backup.chain_id, Backup.binlog_start)
                .where(Backup.container == container, Backup.kind == "fullbackup")
                .order_by(Backup.backup_at.desc(), Backup.id.desc()).limit(1))).one_or_none()
            if chosen_full:
                selected.append(chosen_full.id)
        names = set(await db.scalars(
            select(Backup.database).where(Backup.container == container, Backup.kind == "databases").distinct()))
        wanted = names if databases is None else (names & set(databases)) | ({USERS_DATABASE} & names)
        for name in sorted(wanted):
            newest = await db.scalar(
                select(Backup.id).where(Backup.container == container, Backup.kind == "databases", Backup.database == name)
                .order_by(Backup.backup_at.desc(), Backup.id.desc()).limit(1))
            selected.append(newest)
        if chosen_full and chosen_full.chain_id is not None:
            selected += list(await db.scalars(
                select(Backup.id).where(Backup.container == container, Backup.kind == "binlog",
                                        Backup.chain_id == chosen_full.chain_id)
                .order_by(Backup.binlog_no)))
        if not selected:
            raise RunnerError(409, "Belum ada file untuk diuji.")
        job = Job(runner=runner, container=container, kind="restore_test", status=STATUS_WAITING, requested_by=requested_by)
        db.add(job)
        await db.flush()
        for backup_id in dict.fromkeys(selected):
            db.add(JobFile(job_id=job.id, backup_id=backup_id))
    return job
