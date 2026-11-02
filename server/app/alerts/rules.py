"""Alert dihitung dengan aturan biasa, bukan AI. Fungsi murni: jam dan data diterima sebagai argumen."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.backup_control import cron
from app.core.config import Settings
from app.nas.service import NasView

USERS_DUMP = "_users"


@dataclass
class ContainerState:
    name: str
    active: bool
    registered_at: datetime
    origin: str = "file"
    heartbeat_at: datetime | None = None
    server: str | None = None
    machine_id: str | None = None
    info: dict = field(default_factory=dict)
    effective: dict[str, str] | None = None      # jadwal yang dilaporkan agen
    requested: dict[str, str] | None = None
    requested_version: int = 0
    applied_version: int = 0
    source: str | None = None
    schedule_error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)        # kind → jumlah file
    last_full: datetime | None = None
    last_binlog: datetime | None = None
    dumps: dict[str, datetime] = field(default_factory=dict)    # database → backup_at terbaru
    open_integrity: list[tuple[int, str]] = field(default_factory=list)   # (id, relpath)
    failed_requests: list[tuple[int, str, str]] = field(default_factory=list)   # (id, label, status)

    @property
    def has_any_backup(self) -> bool:
        return bool(self.counts)


@dataclass(frozen=True)
class AlertItem:
    key: str
    kind: str
    level: str          # bad | warn
    cond: str           # hanya nama dan umur, tanpa isi data atau log
    container: str | None
    detail: str = ""    # tambahan khusus dashboard, tidak pernah dikirim ke Telegram
    short: str = ""     # label pendek untuk status di daftar container

    @property
    def message(self) -> str:
        """Teks notifikasi: di Telegram tidak ada kolom sumber, jadi nama container ikut disebut."""
        return f"{self.container}: {self.cond}" if self.container else self.cond


def threshold_hours(state: ContainerState, kind: str, fallback: int) -> float | None:
    """1,5× jarak terpanjang jadwal yang dilaporkan agen. None = jenis ini dimatikan, umurnya tidak dinilai."""
    if not state.effective:
        return float(fallback)
    expr = state.effective.get(kind)
    if expr == "off":
        return None
    gap = cron.max_gap_hours(expr) if expr else None
    return gap * 1.5 if gap else float(fallback)


def _age_hours(now: datetime, then: datetime) -> float:
    return (now - then).total_seconds() / 3600


def _full_alert(state: ContainerState, now: datetime, settings: Settings) -> AlertItem | None:
    limit = threshold_hours(state, "full", settings.ALERT_FULL_HOURS)
    if limit is None:
        return None
    key = f"full:{state.name}"
    if state.last_full is None:
        # Menunggu full pertama: baru menjadi alert 36 jam setelah terdaftar, dan hanya bila sudah ada file lain
        if state.has_any_backup and _age_hours(now, state.registered_at) > settings.ALERT_FULL_HOURS:
            return AlertItem(key, "Backup", "bad", "Full backup belum pernah masuk", state.name, short="Full belum masuk")
        return None
    age = _age_hours(now, state.last_full)
    if age <= limit:
        return None
    return AlertItem(
        key, "Backup", "bad", f"Full backup terakhir {cron.hours_text(age)} lalu (batas {cron.hours_text(limit)})",
        state.name, short="Full terlambat",
    )


def _dump_alerts(state: ContainerState, now: datetime, settings: Settings) -> list[AlertItem]:
    items = []
    expected_since = now - timedelta(days=settings.ALERT_DB_EXPECTED_DAYS)
    for database, last in sorted(state.dumps.items()):
        if last < expected_since:
            continue      # database yang sudah lama tidak punya dump tidak lagi dipantau
        is_users = database == USERS_DUMP
        limit = threshold_hours(state, "dump", settings.ALERT_USERS_HOURS if is_users else settings.ALERT_DUMP_HOURS)
        if limit is None:
            continue
        age = _age_hours(now, last)
        if age > limit:
            name = "Dump _users" if is_users else f"Dump {database}"
            items.append(AlertItem(
                f"dump:{state.name}:{database}", "Backup", "bad",
                f"{name} terakhir {cron.hours_text(age)} lalu (batas {cron.hours_text(limit)})",
                state.name, short=f"{name} terlambat",
            ))
    return items


def compute_alerts(states: list[ContainerState], nas: NasView, now: datetime, settings: Settings) -> list[AlertItem]:
    alerts: list[AlertItem] = []
    if nas.state == "down":
        alerts.append(AlertItem("nas", "NAS", "bad", "NAS/NFS tidak ter-mount", None))
    elif nas.state == "hang":
        alerts.append(AlertItem("nas", "NAS", "bad", "NAS tidak merespons", None))
    elif nas.state == "low":
        alerts.append(AlertItem("nas-low", "NAS", "bad",
                                f"Ruang NAS tersisa {nas.free_percent}% (batas {settings.ALERT_DISK_FREE_PERCENT}%)", None))

    active = [state for state in states if state.active]
    heartbeat_limit = timedelta(minutes=settings.ALERT_HEARTBEAT_MINUTES)
    dead = {s.name for s in active if s.heartbeat_at is not None and now - s.heartbeat_at > heartbeat_limit}

    # Semua agen di satu server berhenti: satu alert untuk server itu. Dari sini server mati dan jaringan putus sama.
    by_machine: dict[str, list[ContainerState]] = {}
    for state in active:
        if state.machine_id and state.heartbeat_at is not None:
            by_machine.setdefault(state.machine_id, []).append(state)
    grouped: set[str] = set()
    for machine_id, members in sorted(by_machine.items()):
        if len(members) >= 2 and all(member.name in dead for member in members):
            grouped.update(member.name for member in members)
            server = members[0].server or machine_id
            alerts.append(AlertItem(f"server:{machine_id}", "Agen", "bad",
                                    f"Agen di server {server} tidak merespons", None))

    for state in active:
        for event_id, relpath in state.open_integrity:
            alerts.append(AlertItem(f"integrity:{event_id}", "Integritas", "warn",
                                    "Checksum berbeda untuk file bernama sama", state.name,
                                    detail=relpath, short="Checksum berbeda"))
        if state.heartbeat_at is None:
            alerts.append(AlertItem(f"new:{state.name}", "Agen", "bad",
                                    "Belum ada heartbeat dari agen", state.name, short="Belum ada heartbeat"))
            continue
        if state.name in dead and state.name not in grouped:
            minutes = int((now - state.heartbeat_at).total_seconds() // 60)
            alerts.append(AlertItem(f"heartbeat:{state.name}", "Agen", "bad",
                                    f"Agen tidak mengirim heartbeat {minutes} menit", state.name,
                                    short="Agen tidak merespons"))
        if not state.has_any_backup:
            alerts.append(AlertItem(f"nobackup:{state.name}", "Backup", "bad",
                                    "Belum pernah mengirim backup", state.name, short="Belum ada backup"))
        full = _full_alert(state, now, settings)
        if full:
            alerts.append(full)
        alerts.extend(_dump_alerts(state, now, settings))
        if state.schedule_error:
            alerts.append(AlertItem(f"schedule-error:{state.name}", "Jadwal", "bad",
                                    "Agen menolak jadwal", state.name, detail=state.schedule_error,
                                    short="Jadwal ditolak agen"))
        if state.effective and state.effective.get("full") == "off":
            alerts.append(AlertItem(f"schedule-off:{state.name}", "Jadwal", "bad",
                                    "Jadwal full dimatikan", state.name, short="Jadwal full mati"))
        for request_id, label, status in state.failed_requests:
            alerts.append(AlertItem(f"request:{request_id}", "Permintaan", "warn",
                                    f"Permintaan {label} {status.lower()}", state.name,
                                    short=f"Permintaan backup {status.lower()}"))
    return alerts


def resolved_message(key: str, cond: str, states: dict[str, ContainerState], now: datetime, settings: Settings) -> str:
    """Pesan saat alert hilang. Container nonaktif atau database yang dihapus bukan berarti pulih."""
    container_name = key.split(":")[1] if ":" in key else None
    state = states.get(container_name) if container_name else None
    if state is not None and not state.active:
        return f"Tidak lagi dipantau (container nonaktif): {cond}"
    if key.startswith("dump:") and state is not None:
        database = key.split(":", 2)[2]
        last = state.dumps.get(database)
        if last is None or last < now - timedelta(days=settings.ALERT_DB_EXPECTED_DAYS):
            return f"{state.name}: dump {database} tidak lagi dipantau"
    return f"Pulih: {cond}"
