"""Penjadwalan uji restore dan alert runner. Dijalankan worker tiap menit; tidak menyentuh NAS."""
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry as agents
from app.alerts.rules import AlertItem
from app.backup_control import cron
from app.backups.models import Backup
from app.containers.models import Container, Heartbeat
from app.core.config import settings
from app.runners import service, standby
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, RunnerHeartbeat, Standby

logger = logging.getLogger(__name__)
ACTIVE_JOB = (service.STATUS_WAITING, service.STATUS_RUNNING)


def _local(value: datetime) -> datetime:
    """Cron ditulis dalam jam lokal (DISPLAY_TIMEZONE); DB menyimpan UTC."""
    return value.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE)).replace(tzinfo=None)


async def ensure_defaults(db: AsyncSession) -> None:
    """Container yang belum punya penetapan memakai runner bawaan dan jadwal bawaan, bila runner bawaan diisi."""
    default = settings.DEFAULT_RUNNER
    if not default or default not in runners.entries().active:
        return
    async with db.begin():
        have = set(await db.scalars(select(ContainerSettings.container)))
        for name in await db.scalars(select(Container.name)):
            if name in have or name not in agents.entries().active:
                continue
            db.add(ContainerSettings(container=name, test_runner=default, standby_runner=default,
                                     test_schedule=settings.RESTORE_TEST_DEFAULT, test_full=True))


async def schedule_due_tests(db: AsyncSession) -> list[int]:
    """Satu tugas uji per container yang jadwalnya lewat sejak tugas terakhir. Jadwal yang terlewat tidak diulang."""
    async with db.begin():
        now = _local(await db.scalar(select(func.now())))
        rows = (await db.execute(
            select(ContainerSettings, Container.registered_at).join(Container, Container.name == ContainerSettings.container)
            .where(ContainerSettings.test_runner.is_not(None), ContainerSettings.test_schedule.is_not(None))
        )).all()
        rows = [(r.container, r.test_runner, r.test_schedule, r.test_full, r.test_databases, registered_at)
                for r, registered_at in rows]       # nilai biasa: rollback tugas yang gagal mengosongkan objek ORM
        busy = set(await db.scalars(select(Job.container).where(Job.kind == "restore_test", Job.status.in_(ACTIVE_JOB))))
        last = dict((await db.execute(
            select(Job.container, func.max(Job.created_at)).where(Job.kind == "restore_test").group_by(Job.container))).all())
    created = []
    active_agents, active_runners = agents.entries().active, runners.entries().active
    for container, runner, expr, test_full, test_databases, registered_at in rows:
        if expr == "off" or container in busy or container not in active_agents:
            continue
        if runner not in active_runners:
            continue
        due = cron.next_run(expr, _local(last.get(container) or registered_at))
        if due is None or due > now:
            continue
        try:
            job = await service.create_restore_test(
                db, container, runner, full=test_full, databases=test_databases)
        except service.RunnerError:
            logger.info("restore_test_skipped", extra={"container": container})
            continue
        created.append(job.id)
    return created


async def schedule_standby(db: AsyncSession) -> list[int]:
    """Pemulihan full dan penerapan binlog untuk container yang database cadangannya diaktifkan. Pemulihan pertama
    langsung dibuat; sesudahnya mengikuti jadwal. Satu tugas database cadangan per container pada satu waktu."""
    async with db.begin():
        now = _local(await db.scalar(select(func.now())))
        rows = [(r.container, r.standby_runner, r.standby_full_schedule or settings.STANDBY_FULL_DEFAULT,
                 r.standby_binlog_schedule or settings.STANDBY_BINLOG_DEFAULT)
                for r in await db.scalars(select(ContainerSettings).where(
                    ContainerSettings.standby_enabled.is_(True), ContainerSettings.standby_runner.is_not(None)))]
        last: dict[tuple[str, str], datetime] = {
            (c, k): t for c, k, t in (await db.execute(
                select(Job.container, Job.kind, func.max(Job.created_at)).where(Job.kind.in_(standby.KINDS))
                .group_by(Job.container, Job.kind))).all()}
        have = set(await db.scalars(select(Standby.container)))
    created = []
    active_agents, active_runners = agents.entries().active, runners.entries().active
    for container, runner, full_expr, binlog_expr in rows:
        if container not in active_agents or runner not in active_runners:
            continue
        last_full = last.get((container, standby.KIND_FULL))
        try:
            full_due = last_full is None and container not in have
            if not full_due and last_full is not None and full_expr != "off":
                due = cron.next_run(full_expr, _local(last_full))
                full_due = due is not None and due <= now
            if full_due:
                job = await standby.create_full_job(db, container, runner)
            else:
                last_apply = max((t for k in standby.KINDS if (t := last.get((container, k)))), default=None)
                if container not in have or binlog_expr == "off":
                    continue
                due = cron.next_run(binlog_expr, _local(last_apply)) if last_apply else now
                if due is None or due > now:
                    continue
                job = await standby.create_apply_job(db, container, runner)
        except service.RunnerError:
            logger.info("standby_skipped", extra={"container": container})
            continue
        if job is not None:
            created.append(job.id)
    return created


async def runner_alerts(db: AsyncSession) -> list[AlertItem]:
    """Runner mati, uji gagal, dan uji tidak terlaksana. Dinilai dengan aturan biasa."""
    items: list[AlertItem] = []
    now = await db.scalar(select(func.now()))
    beats = {r.runner: r for r in await db.scalars(select(RunnerHeartbeat))}
    limit = settings.ALERT_HEARTBEAT_MINUTES * 60
    for name in sorted(runners.entries().active):
        beat = beats.get(name)
        if beat is None:
            items.append(AlertItem(f"runner:{name}", "Runner", "bad", f"Runner {name} belum mengirim heartbeat", None))
        elif (now - beat.at).total_seconds() > limit:
            minutes = int((now - beat.at).total_seconds() // 60)
            items.append(AlertItem(f"runner:{name}", "Runner", "bad",
                                   f"Runner {name} tidak mengirim heartbeat {minutes} menit", None))

    active_agents = agents.entries().active
    latest = (
        select(Job.container, func.max(Job.id).label("id")).where(
            Job.kind == "restore_test", Job.status.in_(("Lolos", "Gagal", "Tidak terlaksana"))).group_by(Job.container)
    ).subquery()
    for job in await db.scalars(select(Job).join(latest, Job.id == latest.c.id)):
        if job.container not in active_agents:
            continue
        if job.status == "Gagal":
            items.append(AlertItem(f"restoretest:{job.container}", "Uji restore", "bad",
                                   "Uji restore gagal", job.container, detail=job.note or "", short="Uji restore gagal"))
        elif job.status == "Tidak terlaksana":
            items.append(AlertItem(f"restorerun:{job.container}", "Uji restore", "warn",
                                   "Uji restore tidak terlaksana", job.container, detail=job.note or "",
                                   short="Uji restore tidak terlaksana"))

    machines = dict((await db.execute(select(Heartbeat.container, Heartbeat.machine_id))).all())
    items.extend(await _standby_alerts(db, now, beats, machines, active_agents))
    return items


async def _standby_alerts(db: AsyncSession, now: datetime, beats: dict, machines: dict, active_agents) -> list[AlertItem]:
    """Database cadangan: terhenti, restore gagal, tertinggal, full terlambat, dan runner satu server."""
    items: list[AlertItem] = []
    configured = {r.container: r for r in await db.scalars(select(ContainerSettings).where(
        ContainerSettings.standby_enabled.is_(True)))}
    states = {r.container: r for r in await db.scalars(select(Standby))}
    for container, cfg in configured.items():
        if container not in active_agents:
            continue
        beat = beats.get(cfg.standby_runner or "")
        if beat and beat.machine_id and beat.machine_id == machines.get(container):
            items.append(AlertItem(f"standbyban:{container}", "Database cadangan", "bad",
                                   f"Runner cadangan {cfg.standby_runner} satu server dengan container", container,
                                   short="Runner cadangan satu server"))
    finished = (service.STATUS_NOT_RUN, "Gagal", "Lolos")
    latest = (select(Job.container, func.max(Job.id).label("id")).where(
        Job.kind.in_(standby.KINDS), Job.status.in_(finished)).group_by(Job.container)).subquery()
    for job in await db.scalars(select(Job).join(latest, Job.id == latest.c.id)):
        if job.container not in active_agents:
            continue
        if job.status == "Gagal":
            items.append(AlertItem(f"standbyrun:{job.container}", "Database cadangan", "bad",
                                   "Restore database cadangan gagal", job.container, detail=job.note or "",
                                   short="Restore cadangan gagal"))
        elif job.status == service.STATUS_NOT_RUN:
            items.append(AlertItem(f"standbyrun:{job.container}", "Database cadangan", "warn",
                                   "Restore database cadangan tidak terlaksana", job.container, detail=job.note or "",
                                   short="Restore cadangan tidak terlaksana"))
    for container, state in states.items():
        cfg = configured.get(container)
        if cfg is None or container not in active_agents:
            continue
        if state.full_backup_id is not None and state.applied_binlog_no is None:
            items.append(AlertItem(f"standby:{container}", "Database cadangan", "bad",
                                   "Penerapan binlog database cadangan terhenti sampai pemulihan full berikutnya",
                                   container, short="Database cadangan terhenti"))
            continue
        if state.chain_id is not None and state.applied_binlog_no is not None:
            oldest = await db.scalar(select(func.min(Backup.backup_at)).where(
                Backup.container == container, Backup.kind == "binlog", Backup.chain_id == state.chain_id,
                Backup.binlog_no > state.applied_binlog_no))
            if oldest is not None and (now - oldest).total_seconds() > settings.STANDBY_LAG_HOURS * 3600:
                hours = int((now - oldest).total_seconds() // 3600)
                items.append(AlertItem(f"standbylag:{container}", "Database cadangan", "warn",
                                       f"Database cadangan tertinggal {hours} jam dari binlog yang sudah ada", container,
                                       short="Database cadangan tertinggal"))
        expr = cfg.standby_full_schedule or settings.STANDBY_FULL_DEFAULT
        gap = cron.max_gap_hours(expr) if expr != "off" else None
        limit = max(gap * 1.5, 36) if gap else None
        if limit and state.restored_at and (now - state.restored_at).total_seconds() > limit * 3600:
            hours = int((now - state.restored_at).total_seconds() // 3600)
            items.append(AlertItem(f"standbyfull:{container}", "Database cadangan", "warn",
                                   f"Pemulihan full database cadangan terakhir {hours} jam lalu", container,
                                   short="Full cadangan terlambat"))
    return items
