"""Tampilan dan pengaturan runner untuk dashboard. Tugas dan hasilnya tetap dikelola app.runners."""
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry as agents
from app.backup_control import cron
from app.backups.models import Backup
from app.core.config import settings
from app.runners import service as runner_service
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, RestoreTest, RunnerHeartbeat

KIND_LABEL = {"full": "Full", "dump": "Dump", "users": "Akun (_users)", "binlog": "Rantai binlog"}


@dataclass
class RunnerRow:
    name: str
    active: bool
    heartbeat: RunnerHeartbeat | None = None
    status: str = "wait"          # ok | bad | off | wait
    status_text: str = ""
    running: Job | None = None
    containers: list[str] = field(default_factory=list)


async def runner_rows(db: AsyncSession) -> list[RunnerRow]:
    entries = runners.entries()
    now = await db.scalar(select(func.now()))
    beats = {b.runner: b for b in await db.scalars(select(RunnerHeartbeat))}
    running = {j.runner: j for j in await db.scalars(select(Job).where(Job.status == runner_service.STATUS_RUNNING))}
    assigned: dict[str, set[str]] = {}
    for row in await db.scalars(select(ContainerSettings)):
        for runner in (row.test_runner, row.standby_runner):
            if runner:
                assigned.setdefault(runner, set()).add(row.container)
    limit = settings.ALERT_HEARTBEAT_MINUTES * 60
    rows = []
    for name in sorted(entries.all_names):
        row = RunnerRow(name=name, active=name in entries.active, heartbeat=beats.get(name),
                        running=running.get(name), containers=sorted(assigned.get(name, ())))
        if not row.active:
            row.status, row.status_text = "off", "Nonaktif"
        elif row.heartbeat is None:
            row.status, row.status_text = "wait", "Belum terhubung"
        elif (now - row.heartbeat.at).total_seconds() > limit:
            row.status, row.status_text = "bad", "Tidak merespons"
        else:
            row.status, row.status_text = "ok", "Sehat"
        rows.append(row)
    return rows


async def recent_jobs(db: AsyncSession, *, container: str | None = None, limit: int = 15) -> list[Job]:
    stmt = select(Job).where(Job.kind == "restore_test").order_by(Job.id.desc()).limit(limit)
    if container:
        stmt = stmt.where(Job.container == container)
    return list(await db.scalars(stmt))


async def results_by_job(db: AsyncSession, job_ids: list[int]) -> dict[int, list[RestoreTest]]:
    found: dict[int, list[RestoreTest]] = {}
    if job_ids:
        for row in await db.scalars(select(RestoreTest).where(RestoreTest.job_id.in_(job_ids)).order_by(RestoreTest.id)):
            found.setdefault(row.job_id, []).append(row)
    return found


async def container_settings(db: AsyncSession, container: str) -> ContainerSettings | None:
    return await db.get(ContainerSettings, container)


def schedule_form(expr: str | None, allowed: tuple[str, ...] = ("off", "day", "week", "custom")) -> dict:
    """Nilai awal form dari cron yang tersimpan. Bentuk yang tidak ada di `allowed` tampil sebagai kustom."""
    base = {"mode": "off", "time": "03:30", "dow": "0", "cron": "", "every": "15"}
    if not expr or expr == "off":
        return base
    found = {**base, "mode": "custom", "cron": expr}
    if m := re.fullmatch(r"(\d+) (\d+) \* \* \*", expr):
        found.update(mode="day", time=f"{int(m[2]):02}:{int(m[1]):02}")
    elif m := re.fullmatch(r"(\d+) (\d+) \* \* ([0-6])", expr):
        found.update(mode="week", time=f"{int(m[2]):02}:{int(m[1]):02}", dow=m[3])
    elif m := re.fullmatch(r"0 \*/(\d+) \* \* \*", expr):
        found.update(mode="hours", every=m[1])
    elif expr == "0 * * * *":
        found.update(mode="mins", every="60")
    elif m := re.fullmatch(r"\*/(\d+) \* \* \* \*", expr):
        found.update(mode="mins", every=m[1])
    if found["mode"] not in allowed:
        found["mode"] = "custom"
    return found


class SettingsError(Exception):
    def __init__(self, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail, self.status_code = detail, status_code


async def save_settings(db: AsyncSession, container: str, *, runner: str, schedule: str, full: bool,
                        databases: list[str]) -> None:
    if container not in agents.entries().active:
        raise SettingsError("Container nonaktif.", 409)
    if runner and runner not in runners.entries().active:
        raise SettingsError("Runner tidak dikenal atau nonaktif.")
    if not runner and schedule != "off":
        raise SettingsError("Pilih runner uji sebelum menyalakan jadwal.")
    if not cron.is_valid(schedule):
        raise SettingsError("Jadwal bukan cron 5 kolom yang valid, atau tidak pernah berjalan.")
    async with db.begin():
        known = set(await db.scalars(
            select(Backup.database).where(Backup.container == container, Backup.kind == "databases").distinct()))
        unknown = set(databases) - known
        if unknown:
            raise SettingsError("Database tidak dikenal: " + ", ".join(sorted(unknown)))
        row = await db.get(ContainerSettings, container, with_for_update=True)
        if row is None:
            row = ContainerSettings(container=container)
            db.add(row)
        row.test_runner = runner or None
        row.test_schedule = schedule
        row.test_full = full
        row.test_databases = [d for d in databases if d != "_users"] or None


async def known_databases(db: AsyncSession, container: str) -> list[str]:
    return sorted(d for d in await db.scalars(
        select(Backup.database).where(Backup.container == container, Backup.kind == "databases").distinct())
        if d and d != "_users")


async def last_outcome(db: AsyncSession, container: str) -> tuple[Job | None, datetime | None]:
    """Uji terakhir yang selesai, dan kapan terakhir kali ada hasil Lolos."""
    done = ("Lolos", "Gagal", "Tidak terlaksana")
    job = await db.scalar(select(Job).where(Job.container == container, Job.kind == "restore_test", Job.status.in_(done))
                          .order_by(Job.id.desc()).limit(1))
    passed = await db.scalar(select(func.max(Job.finished_at)).where(
        Job.container == container, Job.kind == "restore_test", Job.status == "Lolos"))
    return job, passed


# ---------- database cadangan ----------
async def standby_state(db: AsyncSession, container: str):
    from app.runners.models import Standby
    return await db.get(Standby, container)


async def standby_runs(db: AsyncSession, container: str, limit: int = 10):
    from app.runners.models import StandbyRun
    return list(await db.scalars(
        select(StandbyRun).where(StandbyRun.container == container).order_by(StandbyRun.id.desc()).limit(limit)))


async def save_standby_settings(db: AsyncSession, container: str, *, enabled: bool, runner: str,
                                full_schedule: str, binlog_schedule: str) -> None:
    if container not in agents.entries().active:
        raise SettingsError("Container nonaktif.", 409)
    if runner and runner not in runners.entries().active:
        raise SettingsError("Runner tidak dikenal atau nonaktif.")
    if enabled and not runner:
        raise SettingsError("Pilih runner cadangan sebelum menyalakan database cadangan.")
    for value in (full_schedule, binlog_schedule):
        if not cron.is_valid(value):
            raise SettingsError("Jadwal bukan cron 5 kolom yang valid, atau tidak pernah berjalan.")
    async with db.begin():
        if runner:
            beat_machine = await db.scalar(select(RunnerHeartbeat.machine_id).where(RunnerHeartbeat.runner == runner))
            from app.containers.models import Heartbeat
            own_machine = await db.scalar(select(Heartbeat.machine_id).where(Heartbeat.container == container))
            if beat_machine and own_machine and beat_machine == own_machine:
                raise SettingsError("Runner ini satu server dengan container; pilih runner di server lain.")
        row = await db.get(ContainerSettings, container, with_for_update=True)
        if row is None:
            row = ContainerSettings(container=container, test_full=True)
            db.add(row)
        row.standby_enabled = enabled
        row.standby_runner = runner or None
        row.standby_full_schedule = full_schedule
        row.standby_binlog_schedule = binlog_schedule
