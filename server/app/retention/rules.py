"""Kebijakan retensi sebagai fungsi murni: daftar backup masuk, daftar yang boleh dihapus keluar.

Aturan:
- Full dan dump (termasuk `_users`): semua disimpan sampai `daily_days`; sesudah itu satu per bulan (yang paling awal
  di bulan itu) sampai `months` bulan; yang lebih tua dihapus.
- Binlog: dihapus sesudah `binlog_days`.
- Tidak pernah dihapus, berapa pun umurnya: full dan dump terbaru di tiap (container, jenis, database); full terakhir yang lolos
  uji, dump terakhir yang lolos uji tiap database, dan binlog serantai dengan full itu mulai dari `binlog_start`;
  file milik tugas yang belum selesai; dan versi yang belum ditinjau."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

BINLOG_NUMBER = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class Item:
    id: int
    container: str
    kind: str                  # fullbackup | databases | binlog
    database: str | None
    backup_at: datetime
    binlog_no: int | None = None
    chain_id: int | None = None
    binlog_start: str | None = None


@dataclass(frozen=True)
class Policy:
    daily_days: int = 30
    months: int = 12
    binlog_days: int = 3


def binlog_start_number(name: str | None) -> int | None:
    match = BINLOG_NUMBER.search(name or "")
    return int(match.group(1)) if match else None


def _month(value: datetime) -> tuple[int, int]:
    return value.year, value.month


def protected_ids(items: list[Item], passed_ids: set[int], busy_ids: set[int], unreviewed_ids: set[int]) -> set[int]:
    """`passed_ids`: backup yang pernah lolos uji. `busy_ids`: file tugas yang belum selesai."""
    keep = set(busy_ids) | set(unreviewed_ids)
    newest: dict[tuple, Item] = {}
    for item in items:
        if item.kind == "binlog":
            continue        # binlog hanya dilindungi lewat rantai full yang lolos uji
        group = (item.container, item.kind, item.database)
        if group not in newest or (item.backup_at, item.id) > (newest[group].backup_at, newest[group].id):
            newest[group] = item
    keep.update(item.id for item in newest.values())

    last_pass: dict[tuple, Item] = {}
    for item in items:
        if item.id in passed_ids and item.kind in ("fullbackup", "databases"):
            group = (item.container, item.kind, item.database)
            if group not in last_pass or (item.backup_at, item.id) > (last_pass[group].backup_at, last_pass[group].id):
                last_pass[group] = item
    keep.update(item.id for item in last_pass.values())

    for (container, kind, _database), full in last_pass.items():
        start = binlog_start_number(full.binlog_start)
        if kind != "fullbackup" or full.chain_id is None or start is None:
            continue
        keep.update(
            item.id for item in items
            if item.container == container and item.kind == "binlog" and item.chain_id == full.chain_id
            and item.binlog_no is not None and item.binlog_no >= start)
    return keep


def plan(items: list[Item], now: datetime, policy: Policy, keep: set[int]) -> dict[int, str]:
    """id → alasan penghapusan, hanya untuk yang tidak dilindungi."""
    daily_cutoff = now - timedelta(days=policy.daily_days)
    binlog_cutoff = now - timedelta(days=policy.binlog_days)
    month_cutoff = _month_start_back(now, policy.months)
    first_in_month: dict[tuple, Item] = {}
    for item in items:
        if item.kind == "binlog" or item.backup_at >= daily_cutoff:
            continue
        key = (item.container, item.kind, item.database, _month(item.backup_at))
        if key not in first_in_month or (item.backup_at, item.id) < (first_in_month[key].backup_at, first_in_month[key].id):
            first_in_month[key] = item
    monthly_keep = {item.id for item in first_in_month.values() if item.backup_at >= month_cutoff}

    doomed: dict[int, str] = {}
    for item in items:
        if item.id in keep:
            continue
        if item.kind == "binlog":
            if item.backup_at < binlog_cutoff:
                doomed[item.id] = f"binlog lebih tua dari {policy.binlog_days} hari"
        elif item.backup_at < daily_cutoff and item.id not in monthly_keep:
            doomed[item.id] = (f"lebih tua dari {policy.months} bulan" if item.backup_at < month_cutoff
                               else f"lebih tua dari {policy.daily_days} hari dan bukan yang pertama bulan itu")
    return doomed


def _month_start_back(now: datetime, months: int) -> datetime:
    """Awal bulan, `months` bulan sebelum bulan ini."""
    index = now.year * 12 + (now.month - 1) - months
    return datetime(index // 12, index % 12 + 1, 1)
