"""Versi agen dan runner yang terpasang dibanding versi terbaru.

Hanya penanda di dashboard: LumbungDB tetap melayani agen dan runner versi lama, karena update dikerjakan admin di tiap
server dan tidak terjadi serentak. Versi yang tidak dikenali (mis. dev) tidak ditandai.
"""
import re

from app.core.config import settings

_AGENT = re.compile(r"-a(\d+)$")       # tag image MariaDB, mis. 12.3-a2: versi MariaDB lalu versi agen
_RUNNER = re.compile(r"^r(\d+)$")      # tag image runner, mis. r2


def _number(pattern: re.Pattern, value: str | None) -> int | None:
    match = pattern.search(value or "")
    return int(match.group(1)) if match else None


def agent_update(reported: str | None) -> str | None:
    """Tag image yang perlu dipasang bila agen lebih lama dari versi terbaru, dengan versi MariaDB container itu
    sendiri (mis. 11.4-a1 menjadi 11.4-a2). None bila sudah terbaru atau versinya tidak dikenali."""
    latest = _number(_AGENT, settings.IMAGE_TAG_DEFAULT)
    current = _number(_AGENT, reported)
    if latest is None or current is None or current >= latest:
        return None
    return _AGENT.sub(f"-a{latest}", reported)


def runner_update(reported: str | None) -> str | None:
    """Versi runner terbaru bila yang terpasang lebih lama. None bila sudah terbaru atau versinya tidak dikenali."""
    latest = _number(_RUNNER, settings.RUNNER_VERSION_LATEST)
    current = _number(_RUNNER, reported)
    if latest is None or current is None or current >= latest:
        return None
    return settings.RUNNER_VERSION_LATEST
