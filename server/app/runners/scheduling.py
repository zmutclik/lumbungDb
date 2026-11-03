"""Penjadwalan uji restore dan alert runner. Dijalankan worker tiap menit; tidak menyentuh NAS."""
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry as agents
from app.alerts.rules import AlertItem
from app.backup_control import cron
from app.containers.models import Container, Heartbeat
from app.core.config import settings
from app.runners import service
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, RunnerHeartbeat

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


async def runner_alerts(db: AsyncSession) -> list[AlertItem]:
    """Runner mati, uji gagal, uji tidak terlaksana, dan larangan satu server. Dinilai dengan aturan biasa."""
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
    for row in await db.scalars(select(ContainerSettings)):
        beat = beats.get(row.test_runner or "")
        machine = machines.get(row.container)
        if row.container in active_agents and beat and beat.machine_id and beat.machine_id == machine:
            items.append(AlertItem(f"restoreban:{row.container}", "Uji restore", "bad",
                                   f"Runner uji {row.test_runner} satu server dengan container", row.container,
                                   short="Runner satu server"))
    return items
