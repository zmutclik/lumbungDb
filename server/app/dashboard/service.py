"""Menyusun data tampilan dari keadaan container dan alert. Tanpa render dan tanpa akses Request."""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.rules import USERS_DUMP, AlertItem, ContainerState, threshold_hours
from app.backup_control import cron
from app.backup_control.models import ControlLog
from app.backups.models import Backup
from app.core.config import settings

DISPLAY_TZ = ZoneInfo(settings.DISPLAY_TIMEZONE)
NOT_CONNECTED = "Belum terhubung"
DAY_NAMES = ("Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu")
_LEVEL_ORDER = {"wait": 0, "bad": 0, "warn": 1, "ok": 2, "off": 3}
REQUEST_PILL = {
    "Menunggu": "wait", "Berjalan": "wait", "Mengirim": "wait", "Selesai": "ok", "Gagal": "bad",
    "Dibatalkan": "off", "Sudah berjalan": "off", "Ditolak": "warn", "Kedaluwarsa": "warn", "Tidak selesai": "bad",
}
SCHEDULE_KINDS = (("full", "Full"), ("dump", "Dump"), ("binlog", "Arsip binlog"))
OFF_NOTES = {
    "full": "Full mati. Tidak ada full baru, dan alert jadwal full dimatikan aktif selama jadwal ini mati.",
    "dump": "Dump mati. Restore per database dan dump _users tidak lagi diperbarui. Umur dump tidak dinilai.",
    "binlog": "Binlog mati. Pemulihan ke titik waktu tidak bisa mencapai data terbaru.",
}


def local_now(now_utc: datetime) -> datetime:
    return now_utc.replace(tzinfo=UTC).astimezone(DISPLAY_TZ).replace(tzinfo=None)


def to_local_date(value: datetime) -> date:
    return value.replace(tzinfo=UTC).astimezone(DISPLAY_TZ).date()


@dataclass
class ContainerView:
    state: ContainerState
    alerts: list[AlertItem]
    status: str       # off | wait-hb | wait-backup | active
    level: str        # bad | warn | ok | wait | off
    pill_text: str
    pill_class: str

    @property
    def name(self) -> str:
        return self.state.name


def build_view(state: ContainerState, alerts: list[AlertItem]) -> ContainerView:
    own = [alert for alert in alerts if alert.container == state.name
           or (state.machine_id and alert.key == f"server:{state.machine_id}")]
    if not state.active:
        return ContainerView(state, [], "off", "off", "Nonaktif", "off")
    if state.heartbeat_at is None:
        return ContainerView(state, own, "wait-hb", "wait", "Menunggu heartbeat pertama", "wait")
    if not state.has_any_backup:
        return ContainerView(state, own, "wait-backup", "bad", "Belum ada backup", "bad")
    level = "bad" if any(a.level == "bad" for a in own) else "warn" if own else "ok"
    if not own:
        if state.last_full is None:
            return ContainerView(state, own, "active", "wait", "Menunggu full pertama", "wait")
        return ContainerView(state, own, "active", "ok", "OK", "ok")
    text = (own[0].short or own[0].cond) if len(own) == 1 else f"{len(own)} masalah"
    return ContainerView(state, own, "active", level, text, level)


def build_views(states: list[ContainerState], alerts: list[AlertItem]) -> list[ContainerView]:
    views = [build_view(state, alerts) for state in states]
    return sorted(views, key=lambda v: (_LEVEL_ORDER[v.level], v.name))


@dataclass
class ServerGroup:
    title: str
    views: list[ContainerView] = field(default_factory=list)
    ip: str | None = None

    @property
    def problems(self) -> int:
        return sum(1 for view in self.views if view.level in ("bad", "warn"))


def group_by_server(views: list[ContainerView]) -> list[ServerGroup]:
    """Server dibaca dari heartbeat, tidak pernah diisi manual. Pengelompokan memakai machine-id bila ada."""
    groups: dict[str, ServerGroup] = {}
    for view in views:
        state = view.state
        key = state.machine_id or state.server or NOT_CONNECTED
        title = state.server or state.machine_id or NOT_CONNECTED
        group = groups.setdefault(key, ServerGroup(title))
        group.views.append(view)
        group.ip = group.ip or state.info.get("ip")
    return sorted(groups.values(), key=lambda g: min(_LEVEL_ORDER[v.level] for v in g.views))


# ---------- linimasa ----------
@dataclass(frozen=True)
class TimelineCell:
    css: str
    text: str
    description: str
    day: str
    link: bool


def _due_on(expr: str | None, day: date, now_local: datetime) -> bool:
    """Jadwal hari itu sudah lewat. Backup yang jadwalnya belum lewat bukan lubang."""
    if not expr or expr == "off":
        return False
    runs = cron.runs_on(expr, day)
    return bool(runs) and runs[0] <= now_local


def timeline_cell(
    state: ContainerState, day: date, files: list[tuple[str, str | None]], now_local: datetime,
    disabled_day: date | None, expected_dbs: set[str],
) -> TimelineCell:
    label = f"{day.day:02}-{day.month:02}"
    iso = day.isoformat()
    if disabled_day and day > disabled_day:
        return TimelineCell("off", "", f"{label}: nonaktif", iso, False)
    has_full = any(kind == "fullbackup" for kind, _ in files)
    dumped = {database for kind, database in files if kind == "databases"}
    has_dump = expected_dbs.issubset(dumped) if expected_dbs else bool(dumped)
    if day < to_local_date(state.registered_at) and not files:
        return TimelineCell("pre", "", f"{label}: belum terdaftar", iso, False)
    effective = state.effective or {}
    full_due = _due_on(effective.get("full"), day, now_local)
    dump_due = _due_on(effective.get("dump"), day, now_local)
    text = " ".join(part for part, present in (("F", has_full), ("D", has_dump)) if present)
    if not has_full and not has_dump and not dumped:
        if not full_due and not dump_due:
            return TimelineCell("pre", "", f"{label}: belum waktunya backup", iso, False)
        return TimelineCell("none", "✕", f"{label}: tidak ada backup", iso, True)
    missing_full, missing_dump = full_due and not has_full, dump_due and not has_dump
    if missing_full or missing_dump:
        reason = "full tidak masuk" if missing_full else "dump tidak lengkap"
        return TimelineCell("part", text or "–", f"{label}: {reason}", iso, True)
    return TimelineCell("full", text or "–", f"{label}: backup masuk", iso, True)


async def timeline(
    db: AsyncSession, views: list[ContainerView], days: int, now_utc: datetime,
) -> tuple[list[date], list[tuple[ContainerView, list[TimelineCell]]]]:
    now_local = local_now(now_utc)
    today = now_local.date()
    day_list = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    start = datetime.combine(day_list[0], datetime.min.time(), DISPLAY_TZ).astimezone(UTC).replace(tzinfo=None)
    rows = (await db.execute(
        select(Backup.container, Backup.kind, Backup.database, Backup.backup_at)
        .where(Backup.kind != "binlog", Backup.backup_at >= start)
    )).all()
    files: dict[tuple[str, date], list[tuple[str, str | None]]] = {}
    expected: dict[str, set[str]] = {}
    for row in rows:
        files.setdefault((row.container, to_local_date(row.backup_at)), []).append((row.kind, row.database))
    for view in views:
        expected[view.name] = {
            database for database, last in view.state.dumps.items()
            if database != USERS_DUMP and last >= start
        }
    disabled = await _disabled_days(db)
    result = []
    for view in views:
        cells = [
            timeline_cell(view.state, day, files.get((view.name, day), []), now_local,
                          disabled.get(view.name) if not view.state.active else None, expected[view.name])
            for day in day_list
        ]
        result.append((view, cells))
    return day_list, result


async def _disabled_days(db: AsyncSession) -> dict[str, date]:
    rows = (await db.execute(
        select(ControlLog.container, ControlLog.at).where(ControlLog.action == "Nonaktifkan")
    )).all()
    return {row.container: to_local_date(row.at) for row in rows}


async def disabled_at(db: AsyncSession, container: str) -> datetime | None:
    return await db.scalar(
        select(ControlLog.at).where(ControlLog.container == container, ControlLog.action == "Nonaktifkan")
        .order_by(ControlLog.id.desc()).limit(1)
    )


# ---------- jadwal: tampilan dan form ----------
def threshold_text(state: ContainerState, kind: str) -> str:
    if kind == "binlog":
        return "Rantai binlog, bukan umur file"
    fallback = settings.ALERT_FULL_HOURS if kind == "full" else settings.ALERT_DUMP_HOURS
    limit = threshold_hours(state, kind, fallback)
    return "Tidak dinilai" if limit is None else cron.hours_text(limit)


def next_label(expr: str | None, now_utc: datetime) -> str:
    if not expr or expr == "off":
        return "—"
    run = cron.next_run(expr, local_now(now_utc))
    if run is None:
        return "—"
    months = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
    return f"{run.day} {months[run.month - 1]} {run:%H:%M}"


def schedule_rows(state: ContainerState, now_utc: datetime) -> list[dict]:
    if not state.effective:
        return []
    return [
        {
            "kind": kind, "title": title, "expr": state.effective[kind], "label": cron.label(state.effective[kind]),
            "off": state.effective[kind] == "off", "next": next_label(state.effective[kind], now_utc),
            "threshold": threshold_text(state, kind),
        }
        for kind, title in SCHEDULE_KINDS
    ]


def form_from_cron(kind: str, expr: str) -> dict:
    """Memecah cron menjadi isian form: harian, mingguan, tiap N jam atau menit, kustom, atau mati."""
    values = {"mode": "custom", "time": "03:00" if kind == "dump" else "01:00", "dow": "0",
              "every": "15" if kind == "binlog" else "12", "cron": ""}
    if expr == "off":
        values["mode"] = "off"
        return values
    parts = expr.split(" ")
    numeric = len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit()
    if kind != "binlog" and numeric and parts[2:4] == ["*", "*"] and parts[4] == "*":
        values.update(mode="day", time=f"{int(parts[1]):02}:{int(parts[0]):02}")
    elif kind != "binlog" and numeric and parts[2:4] == ["*", "*"] and parts[4].isdigit():
        values.update(mode="week", time=f"{int(parts[1]):02}:{int(parts[0]):02}", dow=str(int(parts[4]) % 7))
    elif kind != "binlog" and parts[0] == "0" and parts[1] in ("*/6", "*/8", "*/12") and parts[2:] == ["*", "*", "*"]:
        values.update(mode="hours", every=parts[1][2:])
    elif kind == "binlog" and expr == "0 * * * *":
        values.update(mode="mins", every="60")
    elif kind == "binlog" and parts[0] in ("*/5", "*/15", "*/30") and parts[1:] == ["*", "*", "*", "*"]:
        values.update(mode="mins", every=parts[0][2:])
    else:
        values["cron"] = expr
    return values


def cron_from_form(kind: str, form) -> str:
    mode = str(form.get(f"{kind}_mode", ""))
    if mode == "off":
        return "off"
    if mode in ("day", "week"):
        hour, _, minute = str(form.get(f"{kind}_time", "")).partition(":")
        if not (hour.isdigit() and minute.isdigit()):
            return "invalid"
        base = f"{int(minute)} {int(hour)} * *"
        return f"{base} *" if mode == "day" else f"{base} {form.get(f'{kind}_dow', '0')}"
    if mode == "hours":
        return f"0 */{form.get(f'{kind}_every', '12')} * * *"
    if mode == "mins":
        every = str(form.get(f"{kind}_every", "15"))
        return "0 * * * *" if every == "60" else f"*/{every} * * * *"
    return " ".join(str(form.get(f"{kind}_cron", "")).split())


def _near_in_day(first: datetime, second: datetime) -> bool:
    day = 86400
    difference = abs((first - second).total_seconds()) % day
    return min(difference, day - difference) < 1800


def form_rows(state: ContainerState, states: list[ContainerState], form, now_utc: datetime) -> tuple[list[dict], dict]:
    """Baris form dengan pratinjau dari server. Mengembalikan (baris, nilai cron)."""
    rows, values = [], {}
    now_local = local_now(now_utc)
    for kind, title in SCHEDULE_KINDS:
        if form is None:
            fields = form_from_cron(kind, (state.requested or state.effective or {}).get(kind, "off"))
            expr = (state.requested or state.effective or {}).get(kind, "off")
        else:
            fields = {name: str(form.get(f"{kind}_{name}", "")) for name in ("mode", "time", "dow", "every", "cron")}
            expr = cron_from_form(kind, form)
        values[kind] = expr
        row = {"kind": kind, "title": title, **fields, "error": None, "note": None, "hint": None, "warn": None}
        if expr == "off":
            row["note"] = OFF_NOTES[kind]
        elif not cron.is_valid(expr):
            row["error"] = "Bukan cron 5 kolom yang valid, atau tidak pernah berjalan."
        else:
            hint = f"Berikutnya {next_label(expr, now_utc)}"
            gap = cron.max_gap_hours(expr)
            if kind != "binlog" and gap:
                hint += f" · alert bila lebih tua dari {cron.hours_text(gap * 1.5)}"
            row["hint"] = hint
            if kind == "full":
                row["warn"] = _near_warning(state, states, expr, now_local)
        rows.append(row)
    return rows, values


def _near_warning(state: ContainerState, states: list[ContainerState], expr: str, now_local: datetime) -> str | None:
    mine = cron.next_run(expr, now_local)
    if mine is None or not state.machine_id:
        return None
    for other in states:
        if other.name == state.name or other.machine_id != state.machine_id or not other.active:
            continue
        other_expr = (other.effective or {}).get("full")
        if not other_expr or other_expr == "off":
            continue
        theirs = cron.next_run(other_expr, now_local)
        if theirs and _near_in_day(mine, theirs):
            return f"Berdekatan dengan full {other.name} ({cron.label(other_expr)}) di server yang sama."
    return None


def dump_rows(state: ContainerState, now_utc: datetime) -> list[dict]:
    """Tiap database dinilai sendiri; _users berisi akun, jadi tidak dihitung sebagai database."""
    fallback = {USERS_DUMP: settings.ALERT_USERS_HOURS}
    rows = []
    for database, last in sorted(state.dumps.items(), key=lambda item: (item[0] == USERS_DUMP, item[0])):
        limit = threshold_hours(state, "dump", fallback.get(database, settings.ALERT_DUMP_HOURS))
        late = limit is not None and (now_utc - last).total_seconds() / 3600 > limit
        rows.append({"database": database, "last": last, "late": late, "off": not state.active})
    return rows


_LONG_DAYS = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
_LONG_MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
                "November", "Desember")


def long_date(now_utc: datetime) -> str:
    today = local_now(now_utc)
    return f"{_LONG_DAYS[today.weekday()]}, {today.day} {_LONG_MONTHS[today.month - 1]} {today.year}"


def day_label(day: date) -> str:
    months = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
    return f"{day.day} {months[day.month - 1]}"


def oldest_dump(state: ContainerState) -> datetime | None:
    """Dump terlama di antara dump terakhir tiap database: satu database yang tertinggal langsung terlihat."""
    return min(state.dumps.values()) if state.dumps else None


def disk_used_percent(state: ContainerState) -> int | None:
    disk = state.info.get("disk") or {}
    total, free = disk.get("total"), disk.get("free")
    if not total or free is None:
        return None
    return round((total - free) * 100 / total)


def settings_rows() -> list[tuple[str, str]]:
    """Hanya-baca. Rahasia tidak pernah ditampilkan, cukup keterangan terisi atau kosong."""
    return [
        ("AGENTS_FILE", settings.AGENTS_FILE),
        ("DATA_DIR", f"{settings.DATA_DIR} (mount NFS dari server)"),
        ("MOUNT_MARKER", settings.MOUNT_MARKER or "kosong: cek penanda dimatikan"),
        ("MOUNT_PROBE_TIMEOUT_SECONDS", f"{settings.MOUNT_PROBE_TIMEOUT_SECONDS} detik"),
        ("UPLOAD_MAX_CONCURRENT", str(settings.UPLOAD_MAX_CONCURRENT)),
        ("WEB_CONCURRENCY", str(settings.WEB_CONCURRENCY)),
        ("INSTALL_COMMAND_HOURS", f"{settings.INSTALL_COMMAND_HOURS} jam"),
        ("SESSION_MAX_AGE", f"{settings.SESSION_MAX_AGE // 3600} jam, tidak diperpanjang oleh aktivitas"),
        ("LOGIN_MAX_FAILURES", f"{settings.LOGIN_MAX_FAILURES} kali per username dan alamat IP"),
        ("LOGIN_FAILURE_WINDOW", f"{settings.LOGIN_FAILURE_WINDOW // 60} menit"),
        ("TELEGRAM_TOKEN", "terisi" if settings.TELEGRAM_TOKEN else "kosong: notifikasi hanya masuk log"),
        ("TELEGRAM_CHAT_ID", "terisi" if settings.TELEGRAM_CHAT_ID else "kosong"),
        ("ALERT_HEARTBEAT_MINUTES", f"{settings.ALERT_HEARTBEAT_MINUTES} menit"),
        ("ALERT_FULL_HOURS", f"{settings.ALERT_FULL_HOURS} jam, bila agen belum melaporkan jadwal"),
        ("ALERT_DUMP_HOURS", f"{settings.ALERT_DUMP_HOURS} jam per database, bila agen belum melaporkan jadwal"),
        ("ALERT_USERS_HOURS", f"{settings.ALERT_USERS_HOURS} jam, bila agen belum melaporkan jadwal"),
        ("ALERT_DB_EXPECTED_DAYS", f"{settings.ALERT_DB_EXPECTED_DAYS} hari"),
        ("ALERT_DISK_FREE_PERCENT", f"{settings.ALERT_DISK_FREE_PERCENT}%"),
        ("RETENTION_ENABLED", "ya" if settings.RETENTION_ENABLED else "mati: tidak ada file yang dihapus"),
        ("RETENTION_DAILY_DAYS", f"{settings.RETENTION_DAILY_DAYS} hari: semua full dan dump disimpan"),
        ("RETENTION_MONTHS", f"{settings.RETENTION_MONTHS} bulan: sesudah itu satu per bulan"),
        ("RETENTION_BINLOG_DAYS", f"{settings.RETENTION_BINLOG_DAYS} hari, kecuali rantai full yang lolos uji"),
        ("RENOTIFY_HOURS", f"{settings.RENOTIFY_HOURS} jam"),
        ("BACKUP_REQUEST_TTL_MINUTES", f"{settings.BACKUP_REQUEST_TTL_MINUTES} menit"),
    ]
