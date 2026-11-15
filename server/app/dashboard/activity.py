"""Aktivitas MariaDB per menit untuk halaman container: tile angka terkini dan grafik SVG satu jam terakhir.

Laju per detik = selisih dua cuplikan berurutan dibagi selisih Uptime. Uptime mundur atau penghitung turun berarti
MariaDB restart: titik itu dilewati sehingga garis terputus. Celah heartbeat lebih dari GAP juga memutus garis.
Grafik digambar di server sebagai path SVG (CSP melarang script inline, dan tidak perlu library grafik)."""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import filesize, localtime
from app.reports import rules
from app.reports.models import StatusSample

WINDOW = timedelta(hours=1)
GAP = timedelta(minutes=3)
STALE = timedelta(minutes=5)

# Ukuran viewBox; CSS menskalakan selebar kartu.
WIDTH, HEIGHT = 560, 190
LEFT, RIGHT, TOP, BOTTOM = 58, 10, 10, 24
PLOT_W, PLOT_H = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

RATES = {
    "qps": "Questions", "sessions": "Connections", "bytes_in": "Bytes_received", "bytes_out": "Bytes_sent",
    "select": "Com_select", "insert": "Com_insert", "update": "Com_update", "delete": "Com_delete",
}


def number(value: float | None) -> str:
    """45191.2 → "45.191"; 3.456 → "3,46". Pemisah ribuan titik, desimal koma."""
    if value is None:
        return "—"
    if abs(value) >= 100:
        return f"{value:,.0f}".replace(",", ".")
    if abs(value) >= 10:
        return f"{value:.1f}".replace(".", ",")
    return f"{value:.2f}".replace(".", ",")


def count(value: int | None) -> str:
    """Nilai bulat (koneksi, thread): 9999 → "9.999"."""
    return "—" if value is None else f"{int(value):,}".replace(",", ".")


def tick_label(value: float, step: float) -> str:
    """Label sumbu dengan desimal secukupnya menurut jarak garis bantu: 0, 50, 100 atau 0,25, 0,5."""
    decimals = 0 if step >= 1 else min(3, max(1, -math.floor(math.log10(step)) + (0 if step * 10 % 1 == 0 else 1)))
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def per_second_bytes(value: float | None) -> str:
    return "—" if value is None else f"{filesize(int(round(value)))}/dtk"


def uptime_text(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} hari {hours} jam"
    if hours:
        return f"{hours} jam {minutes} mnt"
    return f"{minutes} mnt"


@dataclass
class Point:
    at: datetime
    rates: dict[str, float]


def rate_points(samples: list[StatusSample]) -> list[Point | None]:
    """Satu Point per pasangan cuplikan; None menandai putusnya garis (restart atau celah heartbeat)."""
    points: list[Point | None] = []
    for before, after in zip(samples, samples[1:], strict=False):
        elapsed = after.uptime - before.uptime
        if elapsed <= 0 or after.at - before.at > GAP:
            points.append(None)
            continue
        rates = {}
        restarted = False
        for key, name in RATES.items():
            if name in before.data and name in after.data:
                delta = after.data[name] - before.data[name]
                if delta < 0:
                    restarted = True
                    break
                rates[key] = delta / elapsed
        points.append(None if restarted else Point(after.at, rates))
    return points


# ---------- geometri grafik ----------
@dataclass
class Series:
    key: str            # kelas CSS warna: s1..s4
    label: str
    path: str = ""
    area: str = ""


@dataclass
class Chart:
    title: str
    series: list[Series]
    y_ticks: list[tuple[float, str]] = field(default_factory=list)
    x_ticks: list[tuple[float, str]] = field(default_factory=list)
    hits: list[tuple[float, float, str]] = field(default_factory=list)   # x, lebar, teks tooltip
    empty: bool = True
    width: int = WIDTH
    height: int = HEIGHT
    left: int = LEFT
    right_edge: int = WIDTH - RIGHT
    bottom_y: int = TOP + PLOT_H


def nice_scale(peak: float) -> tuple[float, float]:
    """Batas atas dan jarak garis bantu (4 langkah) dengan kelipatan 1, 2, 2.5, 5."""
    if peak <= 0:
        return 1.0, 0.25
    magnitude = 10 ** math.floor(math.log10(peak / 4))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step * 4 >= peak:
            return step * 4, step
    return peak, peak / 4    # tidak tercapai


def _smooth(points: list[tuple[float, float]], floor_y: float, top_y: float) -> str:
    """Catmull-Rom → kubik Bezier; titik kendali dijepit ke area plot supaya kurva tidak menembus nol."""
    if len(points) == 1:
        x, y = points[0]
        return f"M{x:.1f},{y:.1f}h0.1"
    out = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, min(floor_y, max(top_y, p1[1] + (p2[1] - p0[1]) / 6)))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, min(floor_y, max(top_y, p2[1] - (p3[1] - p1[1]) / 6)))
        out.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return "".join(out)


def build_chart(title: str, points: list[Point | None], lines: list[tuple[str, str]], fmt, start: datetime,
                end: datetime, area: bool = False, axis_fmt=None) -> Chart:
    """lines: (kunci laju, label). Satu sumbu y untuk semua garis. fmt untuk tooltip, axis_fmt untuk label sumbu."""
    chart = Chart(title, [Series(f"s{i + 1}", label) for i, (_, label) in enumerate(lines)])
    span = (end - start).total_seconds()

    def x_of(at: datetime) -> float:
        return LEFT + PLOT_W * min(1.0, max(0.0, (at - start).total_seconds() / span))

    valid = [p for p in points if p is not None and p.at >= start and any(k in p.rates for k, _ in lines)]
    peak = max((p.rates.get(k, 0) for p in valid for k, _ in lines), default=0)
    top, step = nice_scale(peak)
    floor_y = TOP + PLOT_H

    def y_of(value: float) -> float:
        return floor_y - PLOT_H * value / top

    chart.y_ticks = [(y_of(step * i), axis_fmt(step * i) if axis_fmt else tick_label(step * i, step)) for i in range(5)]
    tick_every = timedelta(minutes=10)
    first = start.replace(second=0, microsecond=0) + timedelta(minutes=(10 - start.minute % 10) % 10)
    tick = first
    while tick <= end:
        chart.x_ticks.append((x_of(tick), localtime(tick, "%H:%M")))
        tick += tick_every
    if not valid:
        return chart
    chart.empty = False

    for series, (key, _) in zip(chart.series, lines, strict=True):
        runs, run = [], []
        for p in points:
            if p is None or p.at < start or key not in p.rates:
                if run:
                    runs.append(run)
                run = []
                continue
            run.append((x_of(p.at), y_of(p.rates[key])))
        if run:
            runs.append(run)
        series.path = "".join(_smooth(r, floor_y, TOP) for r in runs)
        if area:
            series.area = "".join(
                f"{_smooth(r, floor_y, TOP)}L{r[-1][0]:.1f},{floor_y}L{r[0][0]:.1f},{floor_y}Z" for r in runs)

    # Area hover per titik: tooltip bawaan browser lewat <title>.
    xs = [x_of(p.at) for p in valid]
    for i, p in enumerate(valid):
        left_edge = (xs[i - 1] + xs[i]) / 2 if i else max(LEFT, xs[i] - 4)
        right_edge = (xs[i] + xs[i + 1]) / 2 if i + 1 < len(xs) else min(LEFT + PLOT_W, xs[i] + 4)
        text = localtime(p.at, "%H:%M") + "".join(
            f"\n{label}: {fmt(p.rates[key])}" for key, label in lines if key in p.rates)
        chart.hits.append((left_edge, max(1.0, right_edge - left_edge), text))
    return chart


# ---------- konteks halaman ----------
@dataclass
class Tile:
    label: str
    value: str
    note: str = ""


@dataclass
class Activity:
    tiles: list[Tile]
    charts: list[Chart]
    last_at: datetime | None
    stale: bool


async def load(db: AsyncSession, container: str, now: datetime) -> Activity | None:
    """None bila belum ada cuplikan sama sekali dalam satu jam terakhir."""
    start = now - WINDOW
    samples = (await db.execute(
        select(StatusSample).where(StatusSample.container == container, StatusSample.at >= start - GAP)
        .order_by(StatusSample.at))).scalars().all()
    if not samples:
        return None
    points = rate_points(samples)
    latest = samples[-1]
    data = latest.data
    last_rates = points[-1].rates if points and points[-1] is not None else {}
    requests, reads = data.get("Innodb_buffer_pool_read_requests"), data.get("Innodb_buffer_pool_reads")
    hit = rules.hit_ratio(requests, reads) if requests else None
    connected, limit = data.get("Threads_connected"), data.get("max_connections")
    tiles = [
        Tile("Kueri / detik", number(last_rates.get("qps")), "rata-rata menit terakhir"),
        Tile("Koneksi", f"{count(connected)} / {count(limit)}" if connected is not None else "—",
             "tersambung / max_connections"),
        Tile("Thread berjalan", count(data.get("Threads_running")), "sedang mengeksekusi"),
        Tile("Trafik masuk", per_second_bytes(last_rates.get("bytes_in")), "rata-rata menit terakhir"),
        Tile("Trafik keluar", per_second_bytes(last_rates.get("bytes_out")), "rata-rata menit terakhir"),
        Tile("Kueri lambat", count(data.get("Slow_queries")), "sejak MariaDB mulai"),
        Tile("Waktu aktif", uptime_text(latest.uptime), "sejak restart terakhir"),
        Tile("Rasio hit InnoDB", f"{hit:.2f}%".replace(".", ",") if hit is not None else "—", "buffer pool, kumulatif"),
    ]
    charts = [
        build_chart("Kueri per detik", points, [("qps", "Kueri")], number, start, now, area=True),
        build_chart("Sesi baru per detik", points, [("sessions", "Sesi")], number, start, now, area=True),
        build_chart("Trafik jaringan per detik", points, [("bytes_in", "Masuk"), ("bytes_out", "Keluar")],
                    per_second_bytes, start, now, axis_fmt=lambda v: filesize(int(round(v)))),
        build_chart("Perintah per detik", points,
                    [("select", "SELECT"), ("insert", "INSERT"), ("update", "UPDATE"), ("delete", "DELETE")],
                    number, start, now),
    ]
    return Activity(tiles, charts, latest.at, now - latest.at > STALE)
