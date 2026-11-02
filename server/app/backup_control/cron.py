"""Cron 5 kolom: validasi, waktu jalan, jarak terpanjang, dan label. Aturan karakternya sama dengan agen dan helper."""
import functools
import re
from datetime import date, datetime, time, timedelta

_PART = re.compile(r"^(\*|\d+(?:-\d+)?)(?:/(\d+))?$")
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_DAYS = ("Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu")
_SAMPLE_START = date(2026, 1, 1)
_SAMPLE_DAYS = 62    # cukup untuk menilai jadwal bulanan


def _field(text: str, low: int, high: int) -> frozenset[int] | None:
    values: set[int] = set()
    for part in text.split(","):
        match = _PART.match(part)
        if not match:
            return None
        step = int(match.group(2) or 1)
        if match.group(1) == "*":
            first, last = low, high
        else:
            bounds = match.group(1).split("-")
            first = int(bounds[0])
            last = int(bounds[1]) if len(bounds) == 2 else (high if match.group(2) else first)
        if first < low or last > high or first > last or step < 1:
            return None
        values.update(range(first, last + 1, step))
    return frozenset(values)


@functools.lru_cache(maxsize=256)
def parse(expr: str) -> tuple | None:
    if not re.fullmatch(r"[0-9*/,\- ]+", expr or ""):
        return None
    texts = expr.split(" ")
    if len(texts) != 5:
        return None
    fields = [_field(text, low, high) for text, (low, high) in zip(texts, _RANGES, strict=True)]
    if not all(fields):
        return None
    dow = set(fields[4])
    if 7 in dow:
        dow.add(0)
    return fields[0], fields[1], fields[2], fields[3], frozenset(dow), texts[2] == "*", texts[4] == "*"


def _day_matches(parsed: tuple, day: date) -> bool:
    _, _, dom, month, dow, dom_any, dow_any = parsed
    if day.month not in month:
        return False
    in_dom, in_dow = day.day in dom, (day.isoweekday() % 7) in dow
    if dom_any and dow_any:
        return True
    if dom_any:
        return in_dow
    if dow_any:
        return in_dom
    return in_dom or in_dow      # aturan cron: keduanya diisi berarti salah satu cocok


def runs_on(expr: str, day: date) -> list[datetime]:
    parsed = parse(expr)
    if not parsed or not _day_matches(parsed, day):
        return []
    return [datetime.combine(day, time(h, m)) for h in sorted(parsed[1]) for m in sorted(parsed[0])]


def is_valid(expr: str) -> bool:
    """Valid dan benar-benar berjalan: `0 0 31 2 *` tidak pernah jalan, jadi ditolak."""
    if expr == "off":
        return True
    if not parse(expr):
        return False
    return next_run(expr, datetime(2026, 1, 1)) is not None


@functools.lru_cache(maxsize=256)
def max_gap_hours(expr: str) -> float | None:
    """Jarak terpanjang antar jadwal, dasar ambang alert. None bila tidak terhitung."""
    if not parse(expr):
        return None
    runs: list[datetime] = []
    for offset in range(_SAMPLE_DAYS):
        runs.extend(runs_on(expr, _SAMPLE_START + timedelta(days=offset)))
    if len(runs) < 2:
        return None
    return max((b - a for a, b in zip(runs, runs[1:], strict=False)), default=timedelta()).total_seconds() / 3600


def next_run(expr: str, after: datetime, horizon_days: int = 800) -> datetime | None:
    if not parse(expr):
        return None
    for offset in range(horizon_days):
        for run in runs_on(expr, after.date() + timedelta(days=offset)):
            if run > after:
                return run
    return None


def label(expr: str | None) -> str:
    if not expr:
        return "—"
    if expr == "off":
        return "Mati"
    if m := re.fullmatch(r"(\d+) (\d+) \* \* \*", expr):
        return f"Tiap hari {int(m[2]):02}:{int(m[1]):02}"
    if m := re.fullmatch(r"(\d+) (\d+) \* \* ([0-7])", expr):
        return f"Tiap {_DAYS[int(m[3]) % 7]} {int(m[2]):02}:{int(m[1]):02}"
    if m := re.fullmatch(r"0 \*/(\d+) \* \* \*", expr):
        return f"Tiap {m[1]} jam"
    if expr == "0 * * * *":
        return "Tiap 1 jam"
    if m := re.fullmatch(r"\*/(\d+) \* \* \* \*", expr):
        return f"Tiap {m[1]} menit"
    return f"Kustom: {expr}"


def hours_text(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} menit"
    return f"{hours:.1f} jam".replace(".0 jam", " jam").replace(".", ",")
