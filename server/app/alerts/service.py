from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.models import Alert
from app.alerts.rules import AlertItem, ContainerState, compute_alerts, resolved_message
from app.backup_control.models import BackupRequest, BackupSchedule
from app.backup_control.service import request_label
from app.backups.models import Backup, IntegrityEvent
from app.containers.models import Container, Heartbeat
from app.core.config import settings
from app.integrations import telegram
from app.nas import service as nas_service
from app.reports import service as report_service
from app.runners import scheduling as runner_scheduling
from app.nas.service import NasView
from app.agents.dependencies import registry

_REQUEST_BACKUP_KIND = {"full": "fullbackup", "dump": "databases", "binlog": "binlog"}


async def load_states(db: AsyncSession) -> list[ContainerState]:
    entries = registry.entries()
    states: dict[str, ContainerState] = {}
    for row in (await db.execute(select(Container.name, Container.registered_at, Container.origin))).all():
        states[row.name] = ContainerState(
            name=row.name, active=row.name in entries.active, registered_at=row.registered_at, origin=row.origin,
        )
    for row in (await db.execute(select(Heartbeat))).scalars():
        if state := states.get(row.container):
            state.heartbeat_at, state.server, state.machine_id = row.at, row.server, row.machine_id
            state.info = row.info or {}
    for row in (await db.execute(select(BackupSchedule))).scalars():
        if state := states.get(row.container):
            state.effective = {"full": row.effective_full, "dump": row.effective_dump, "binlog": row.effective_binlog}
            state.requested = {"full": row.requested_full, "dump": row.requested_dump, "binlog": row.requested_binlog}
            state.requested_version, state.applied_version = row.requested_version, row.applied_version
            state.source = row.source
            state.schedule_error = row.error_message
    for container, days in (await report_service.disk_forecasts(db, await db.scalar(select(func.now())))).items():
        if state := states.get(container):
            state.disk_full_days = days
    latest_kind: dict[tuple[str, str], datetime] = {}
    for row in (await db.execute(
        select(Backup.container, Backup.kind, Backup.database, func.count().label("files"),
               func.max(Backup.backup_at).label("latest"))
        .group_by(Backup.container, Backup.kind, Backup.database)
    )).all():
        state = states.get(row.container)
        if state is None:
            continue
        state.counts[row.kind] = state.counts.get(row.kind, 0) + row.files
        previous = latest_kind.get((row.container, row.kind))
        latest_kind[(row.container, row.kind)] = max(previous, row.latest) if previous else row.latest
        if row.kind == "fullbackup":
            state.last_full = latest_kind[(row.container, row.kind)]
        elif row.kind == "binlog":
            state.last_binlog = latest_kind[(row.container, row.kind)]
        elif row.database:
            state.dumps[row.database] = row.latest
    for row in (await db.execute(
        select(IntegrityEvent.id, IntegrityEvent.container, IntegrityEvent.relpath)
        .where(IntegrityEvent.reviewed_at.is_(None))
    )).all():
        if state := states.get(row.container):
            state.open_integrity.append((row.id, row.relpath))
    # Permintaan terakhir tiap jenis yang punya hasil. Pulih saat backup jenis itu berikutnya berhasil.
    ranked = (
        select(
            BackupRequest.id, BackupRequest.container, BackupRequest.kind, BackupRequest.database,
            BackupRequest.status, BackupRequest.finished_at,
            func.row_number().over(partition_by=(BackupRequest.container, BackupRequest.kind),
                                   order_by=BackupRequest.id.desc()).label("rank"),
        ).where(BackupRequest.status.in_(("Selesai", "Gagal", "Tidak selesai"))).subquery()
    )
    for row in (await db.execute(select(ranked).where(ranked.c.rank == 1))).all():
        state = states.get(row.container)
        if state is None or row.status == "Selesai":
            continue
        newest = latest_kind.get((row.container, _REQUEST_BACKUP_KIND[row.kind]))
        if newest is not None and row.finished_at is not None and newest > row.finished_at:
            continue
        state.failed_requests.append((row.id, request_label(row.kind, row.database), row.status))
    return sorted(states.values(), key=lambda s: s.name)


async def current_alerts(db: AsyncSession) -> tuple[list[ContainerState], NasView, list[AlertItem], dict, datetime]:
    """Dasar semua halaman dashboard: dinilai saat dibuka, jadi tampilan tidak tertinggal dari pengecek."""
    now = await db.scalar(select(func.now()))
    states = await load_states(db)
    nas = await nas_service.get_view(db)
    alerts = compute_alerts(states, nas, now, settings) + await runner_scheduling.runner_alerts(db)
    since = dict((await db.execute(select(Alert.key, Alert.since))).all())
    return states, nas, alerts, since, now


async def reconcile(db: AsyncSession, states: list[ContainerState], items: list[AlertItem], now: datetime) -> list[str]:
    """Memperbarui tabel alert dan mengembalikan pesan yang perlu dikirim. Pengiriman di luar transaksi."""
    messages: list[str] = []
    wanted = {item.key: item for item in items}
    by_name = {state.name: state for state in states}
    renotify = timedelta(hours=settings.RENOTIFY_HOURS)
    async with db.begin():
        rows = {row.key: row for row in await db.scalars(select(Alert).with_for_update())}
        for key, item in wanted.items():
            row = rows.get(key)
            if row is None:
                db.add(Alert(key=key, container=item.container, cond=item.message[:500], since=now, last_sent=now))
                messages.append(item.message)
            else:
                row.cond = item.message[:500]
                if row.last_sent is None or now - row.last_sent >= renotify:
                    row.last_sent = now
                    messages.append(f"Pengingat: {item.message}")
        gone = [key for key in rows if key not in wanted]
        for key in gone:
            messages.append(resolved_message(key, rows[key].cond, by_name, now, settings))
        if gone:
            await db.execute(delete(Alert).where(Alert.key.in_(gone)))
    return messages


async def run_check(db: AsyncSession, client: httpx.AsyncClient) -> None:
    async with db.begin():
        now = await db.scalar(select(func.now()))
        states = await load_states(db)
        nas = await nas_service.get_view(db)
        runner_items = await runner_scheduling.runner_alerts(db)
    items = compute_alerts(states, nas, now, settings) + runner_items
    messages = await reconcile(db, states, items, now)
    # Tanpa transaksi terbuka. Gagal kirim hanya dicatat di log dan dicoba lagi saat pengingat berikutnya.
    for message in messages:
        await telegram.send_message(
            client, settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID, message, api_base=settings.TELEGRAM_API_BASE,
        )
