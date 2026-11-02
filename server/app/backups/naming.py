"""Aturan nama file upload. Tanpa I/O."""
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings

KINDS = ("fullbackup", "databases", "binlog")
_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_LOCAL_ARCHIVE = re.compile(r"^(?P<base>[^.]+\.\d+)\.\d{8}_\d{6}\.gz$")
_BINLOG = re.compile(r"^[^.]+\.(?P<no>\d{1,9})\.gz$")   # muat di kolom INT
_STAMP = re.compile(r"(\d{8}_\d{6})")
_NAME_TZ = ZoneInfo(settings.DISPLAY_TIMEZONE)   # script di image memakai Asia/Jakarta
_MAX_FUTURE = timedelta(hours=1)
_BINLOG_START = re.compile(r"^[A-Za-z0-9_-]+\.(\d+)$")


class InvalidPath(ValueError):
    pass


@dataclass(frozen=True)
class UploadPath:
    kind: str
    relpath: str             # sudah dinormalisasi
    database: str | None
    filename: str
    binlog_no: int | None


def parse_upload_path(kind: str, relpath: str) -> UploadPath:
    """Maksimal satu segmen untuk fullbackup dan binlog, dua untuk databases; hanya .gz. Upaya ../ ditolak di sini."""
    if kind not in KINDS:
        raise InvalidPath("kind tidak dikenal")
    segments = relpath.split("/")
    if len(segments) != (2 if kind == "databases" else 1) or len(relpath) > 255:
        raise InvalidPath("relpath tidak valid")
    if not all(_SEGMENT.fullmatch(segment) for segment in segments):
        raise InvalidPath("nama file tidak valid")
    if not segments[-1].endswith(".gz"):
        raise InvalidPath("nama file tidak valid")
    if kind == "databases" and len(segments[0]) > 64:
        raise InvalidPath("nama database terlalu panjang")
    filename = segments[-1]
    binlog_no = None
    if kind == "binlog":
        # <nama>.<waktu>.gz hanya nama lokal di container; di NAS hanya ada satu skema versi
        local = _LOCAL_ARCHIVE.fullmatch(filename)
        if local:
            filename = f"{local.group('base')}.gz"
        match = _BINLOG.fullmatch(filename)
        if not match:
            raise InvalidPath("nama binlog tidak valid")
        binlog_no = int(match.group("no"))
        relpath = filename
    return UploadPath(kind, relpath, segments[0] if kind == "databases" else None, filename, binlog_no)


def _parse_stamp(filename: str) -> datetime | None:
    found = _STAMP.findall(filename)
    if not found:
        return None
    try:
        local = datetime.strptime(found[-1], "%Y%m%d_%H%M%S").replace(tzinfo=_NAME_TZ)
    except ValueError:
        return None
    return local.astimezone(UTC).replace(tzinfo=None)


def backup_time(path: UploadPath, mtime_header: str | None, now: datetime) -> datetime:
    """Waktu pembuatan backup (UTC tanpa zona): nama file, lalu X-File-Mtime, lalu waktu diterima.
    Nilai lebih dari 1 jam di depan jam LumbungDB tidak dipakai."""
    latest = now + _MAX_FUTURE
    if path.kind != "binlog":
        stamped = _parse_stamp(path.filename)
        if stamped and stamped <= latest:
            return stamped
    if mtime_header:
        try:
            mtime = datetime.fromtimestamp(int(mtime_header), UTC).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            mtime = None
        if mtime and mtime <= latest:
            return mtime
    return now


def valid_binlog_start(value: str | None) -> str | None:
    if value and len(value) <= 64 and _BINLOG_START.fullmatch(value):
        return value
    return None


def binlog_start_number(value: str) -> int:
    match = _BINLOG_START.fullmatch(value)
    return int(match.group(1)) if match else -1
