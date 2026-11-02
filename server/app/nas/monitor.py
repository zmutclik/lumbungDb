"""Menjalankan probe dengan batas tunggu. Proses yang tersangkut dibiarkan (D state tidak bisa dibunuh), tidak dibuat
proses baru, dan status pulih saat proses itu selesai dengan hasil baik."""
import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)
PROBE = Path(__file__).with_name("probe.py")


@dataclass(frozen=True)
class ProbeResult:
    state: str                 # ok | low | down | hang
    total: int | None = None
    free: int | None = None


def state_from_output(output: bytes, returncode: int) -> ProbeResult:
    if returncode != 0:
        return ProbeResult("down")
    try:
        data = json.loads(output)
    except ValueError:
        return ProbeResult("down")
    if not data.get("mounted"):
        return ProbeResult("down")
    total, free = data.get("total"), data.get("free")
    low = bool(total) and free * 100 / total < settings.ALERT_DISK_FREE_PERCENT
    return ProbeResult("low" if low else "ok", total, free)


class NasMonitor:
    def __init__(self, data_dir: str | None = None, marker: str | None = None, timeout: float | None = None) -> None:
        self.data_dir = data_dir if data_dir is not None else settings.DATA_DIR
        self.marker = marker if marker is not None else settings.MOUNT_MARKER
        self.timeout = timeout if timeout is not None else settings.MOUNT_PROBE_TIMEOUT_SECONDS
        self.process: subprocess.Popen | None = None

    async def check(self) -> ProbeResult:
        if self.process is None:
            self.process = subprocess.Popen(
                [sys.executable, "-I", str(PROBE), self.data_dir, self.marker],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        deadline = asyncio.get_running_loop().time() + self.timeout
        while self.process.poll() is None:
            if asyncio.get_running_loop().time() >= deadline:
                return ProbeResult("hang")
            await asyncio.sleep(0.05)
        process, self.process = self.process, None
        output = process.stdout.read() if process.stdout else b""
        if process.stdout:
            process.stdout.close()
        return state_from_output(output, process.returncode)
