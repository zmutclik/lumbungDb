"""Antrean notifikasi: pesan disimpan dulu, lalu dikirim dan diulang dengan jeda yang makin panjang bila gagal."""
import logging

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.rules import AlertItem
from app.core.config import settings
from app.core.database import db_now_plus
from app.integrations import telegram
from app.notifications.models import Notification

logger = logging.getLogger(__name__)
MAX_TEXT = 3500
LEASE_SECONDS = 120        # pesan yang sedang dikirim tidak diambil proses lain


def enqueue(db: AsyncSession, text: str, *, kind: str = "alert", ref: str | None = None) -> None:
    """Dipanggil di dalam transaksi pemanggil, supaya pesan dan perubahan yang memicunya tersimpan bersama."""
    db.add(Notification(kind=kind, ref=ref, text=text[:MAX_TEXT]))


def configured() -> bool:
    return bool(settings.TELEGRAM_TOKEN and settings.TELEGRAM_CHAT_ID)


def backoff_seconds(attempts: int) -> int:
    return min(60 * 2 ** max(attempts - 1, 0), 3600)


async def deliver_due(db: AsyncSession, client: httpx.AsyncClient, limit: int = 20) -> dict[str, int]:
    """Mengirim pesan yang jatuh tempo. Tanpa transaksi terbuka saat memanggil Telegram."""
    counts = {"sent": 0, "failed": 0, "expired": 0, "logged": 0}
    async with db.begin():
        expired = await db.execute(
            update(Notification).where(
                Notification.status == "Menunggu",
                Notification.created_at < db_now_plus(-settings.NOTIFY_MAX_AGE_HOURS * 3600))
            .values(status="Kedaluwarsa"))
        counts["expired"] = expired.rowcount or 0
        due = list(await db.scalars(
            select(Notification).where(Notification.status == "Menunggu", Notification.next_attempt_at <= func.now())
            .order_by(Notification.id).limit(limit).with_for_update(skip_locked=True)))
        batch = [(n.id, n.text, n.attempts, n.ref) for n in due]
        if batch:
            await db.execute(update(Notification).where(Notification.id.in_([b[0] for b in batch]))
                             .values(next_attempt_at=db_now_plus(LEASE_SECONDS)))
    for notification_id, text, attempts, ref in batch:
        if not configured():
            telegram_ok, status = False, "Hanya log"      # tidak ada tujuan: dicatat di log, tidak diulang
            logger.info("notification_log_only", extra={"text": text})
        else:
            telegram_ok = await telegram.send_message(
                client, settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID, text, api_base=settings.TELEGRAM_API_BASE)
            status = "Terkirim" if telegram_ok else "Menunggu"
        async with db.begin():
            if status == "Menunggu":
                await db.execute(update(Notification).where(Notification.id == notification_id).values(
                    attempts=attempts + 1, next_attempt_at=db_now_plus(backoff_seconds(attempts + 1))))
                counts["failed"] += 1
                continue
            await db.execute(update(Notification).where(Notification.id == notification_id).values(
                status=status, attempts=attempts + 1, sent_at=func.now() if telegram_ok else None))
            counts["sent" if telegram_ok else "logged"] += 1
            if telegram_ok and ref and ref.startswith("report:"):
                from app.reports.models import Report
                await db.execute(update(Report).where(Report.id == int(ref.split(":")[1])).values(sent_at=func.now()))
    return counts


async def purge(db: AsyncSession) -> None:
    async with db.begin():
        await db.execute(delete(Notification).where(
            Notification.status != "Menunggu", Notification.created_at < db_now_plus(-30 * 86400)))


async def recent(db: AsyncSession, limit: int = 20) -> list[Notification]:
    return list(await db.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)))


async def queue_alert(db: AsyncSession) -> list[AlertItem]:
    """Pesan yang tertahan lama berarti Telegram tidak terjangkau atau tokennya salah."""
    if not configured():
        return []
    stuck = await db.execute(select(func.count(), func.min(Notification.created_at)).where(
        Notification.status == "Menunggu", Notification.attempts > 0,
        Notification.created_at < db_now_plus(-settings.NOTIFY_STUCK_MINUTES * 60)))
    count, oldest = stuck.one()
    if not count:
        return []
    return [AlertItem("notify", "Notifikasi", "bad", f"{count} notifikasi Telegram belum terkirim", None,
                      detail="Periksa TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, dan akses ke api.telegram.org.")]
