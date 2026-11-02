"""Adapter tipis Telegram. Tidak menyentuh DB. Token ada di URL, jadi error tidak pernah mencatat URL atau pesan
pengecualian httpx; hanya jenis kesalahannya."""
import logging

import httpx

logger = logging.getLogger(__name__)


async def send_message(
    client: httpx.AsyncClient, token: str, chat_id: str, text: str, *, api_base: str = "https://api.telegram.org",
) -> bool:
    if not token or not chat_id:
        logger.info("notification_log_only", extra={"text": text})
        return False
    try:
        response = await client.post(
            f"{api_base}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=10,
        )
    except httpx.HTTPError as err:
        logger.warning("telegram_failed", extra={"error": type(err).__name__})
        return False
    if response.status_code != 200:
        logger.warning("telegram_failed", extra={"error": f"HTTP {response.status_code}"})
        return False
    return True
