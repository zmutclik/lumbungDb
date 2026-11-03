"""Antrean notifikasi: pesan tidak hilang saat Telegram gagal, diulang dengan jeda, kedaluwarsa, dan terlihat di dashboard."""
import httpx
import pytest
from sqlalchemy import select, text, update

from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.notifications import service
from app.notifications.models import Notification
from tests.test_dashboard import csrf  # noqa: F401


@pytest.fixture
def telegram(monkeypatch):
    calls = {"ok": True, "texts": []}

    async def fake_send(client, token, chat_id, message, **kwargs):
        calls["texts"].append(message)
        return calls["ok"]

    monkeypatch.setattr(service.settings, "TELEGRAM_TOKEN", "tok")
    monkeypatch.setattr(service.settings, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(service.telegram, "send_message", fake_send)
    return calls


async def enqueue(*texts, kind="alert", ref=None):
    async with async_session_factory() as db, db.begin():
        for message in texts:
            service.enqueue(db, message, kind=kind, ref=ref)


async def deliver():
    async with async_session_factory() as db, httpx.AsyncClient() as client:
        return await service.deliver_due(db, client)


async def rows():
    async with async_session_factory() as db:
        return list(await db.scalars(select(Notification).order_by(Notification.id)))


async def test_messages_are_sent_in_order_and_marked(telegram):
    await enqueue("satu", "dua")
    assert (await deliver())["sent"] == 2
    assert telegram["texts"] == ["satu", "dua"]
    assert [(r.status, r.attempts) for r in await rows()] == [("Terkirim", 1), ("Terkirim", 1)]
    assert (await deliver())["sent"] == 0


async def test_failed_send_stays_queued_with_growing_delay(telegram):
    telegram["ok"] = False
    await enqueue("penting")
    assert (await deliver())["failed"] == 1
    row, = await rows()
    assert (row.status, row.attempts) == ("Menunggu", 1)
    assert (await deliver())["failed"] == 0            # belum jatuh tempo: tidak dicoba tiap menit
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE notifications SET next_attempt_at = NOW()"))
    telegram["ok"] = True
    assert (await deliver())["sent"] == 1 and telegram["texts"] == ["penting", "penting"]
    assert (await rows())[0].status == "Terkirim"


def test_backoff_grows_and_is_capped():
    assert [service.backoff_seconds(n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert service.backoff_seconds(30) == 3600


async def test_old_unsent_messages_expire(telegram):
    telegram["ok"] = False
    await enqueue("lama")
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE notifications SET created_at = NOW() - INTERVAL 25 HOUR, next_attempt_at = NOW()"))
    counts = await deliver()
    assert counts["expired"] == 1 and (await rows())[0].status == "Kedaluwarsa" and telegram["texts"] == []


async def test_without_telegram_settings_messages_are_logged_not_retried(monkeypatch):
    monkeypatch.setattr(service.settings, "TELEGRAM_TOKEN", "")
    await enqueue("tanpa tujuan")
    assert (await deliver())["logged"] == 1
    assert (await rows())[0].status == "Hanya log"
    assert (await deliver())["logged"] == 0


async def test_report_sent_at_follows_delivery(telegram):
    from datetime import datetime

    from app.reports.models import Report
    async with async_session_factory() as db, db.begin():
        db.add(Report(kind="daily", period_start=datetime(2026, 11, 1), period_end=datetime(2026, 11, 2),
                      data={"checked": 0, "containers": []}))
    telegram["ok"] = False
    await enqueue("ringkasan", kind="laporan", ref="report:1")
    await deliver()
    async with async_session_factory() as db:
        assert (await db.get(Report, 1)).sent_at is None
        await db.execute(text("UPDATE notifications SET next_attempt_at = NOW()"))
        await db.commit()
    telegram["ok"] = True
    await deliver()
    async with async_session_factory() as db:
        assert (await db.get(Report, 1)).sent_at is not None


async def test_stuck_queue_raises_alert_and_clears(telegram):
    telegram["ok"] = False
    await enqueue("tertahan")
    await deliver()
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE notifications SET created_at = NOW() - INTERVAL 40 MINUTE"))
    async with async_session_factory() as db:
        items = await service.queue_alert(db)
    assert items and items[0].key == "notify" and "1 notifikasi" in items[0].cond
    async with async_session_factory() as db, db.begin():
        await db.execute(update(Notification).values(status="Terkirim"))
    async with async_session_factory() as db:
        assert await service.queue_alert(db) == []


async def test_run_check_keeps_alert_message_when_telegram_is_down(telegram):
    from app.alerts import service as alert_service
    telegram["ok"] = False
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("down"))
    async with async_session_factory() as db, httpx.AsyncClient() as client:
        await alert_service.run_check(db, client)
    row, = await rows()
    assert row.status == "Menunggu" and row.text == "NAS/NFS tidak ter-mount"
    telegram["ok"] = True
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE notifications SET next_attempt_at = NOW()"))
    await deliver()
    assert (await rows())[0].status == "Terkirim"


async def test_alerts_page_lists_notifications(client, csrf, telegram):  # noqa: F811
    telegram["ok"] = False
    await enqueue("NAS/NFS tidak ter-mount")
    await deliver()
    page = await client.get("/alerts")
    assert page.status_code == 200 and "Notifikasi terakhir" in page.text
    assert "NAS/NFS tidak ter-mount" in page.text and "dicoba 1 kali" in page.text
