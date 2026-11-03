"""Aturan laporan kesehatan. Fungsi murni: angka masuk, temuan keluar. Bukan AI."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Finding:
    level: str      # bad | warn
    text: str

    def as_dict(self) -> dict:
        return {"level": self.level, "text": self.text}


def hit_ratio(requests: float, reads: float) -> float | None:
    """Persen permintaan buffer pool yang terlayani dari memori. None bila tidak ada permintaan."""
    if requests <= 0:
        return None
    return max(0.0, min(100.0, (1 - reads / requests) * 100))


def forecast_full_days(points: list[tuple[datetime, float]], min_span_hours: float = 24) -> float | None:
    """Sisa hari sampai ruang bebas habis, regresi linear biasa atas (waktu, ruang bebas). None bila datanya kurang
    atau ruang bebas tidak menyusut."""
    if len(points) < 3:
        return None
    first = points[0][0]
    xs = [(t - first).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    if xs[-1] * 24 < min_span_hours:
        return None
    n = len(points)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / variance
    if slope >= 0:
        return None
    intercept = mean_y - slope * mean_x
    fitted_now = max(0.0, intercept + slope * xs[-1])
    return fitted_now / -slope


def missing_numbers(numbers: list[int]) -> list[int]:
    """Nomor binlog yang hilang di antara yang terkecil dan terbesar."""
    if len(numbers) < 2:
        return []
    present = set(numbers)
    return [n for n in range(min(numbers), max(numbers) + 1) if n not in present]


def connection_findings(peak: float | None, limit: float | None, refused: float, warn_percent: int) -> list[Finding]:
    found = []
    if peak is not None and limit:
        percent = peak / limit * 100
        if percent >= 95:
            found.append(Finding("bad", f"Koneksi puncak {percent:.0f}% dari max_connections ({peak:.0f}/{limit:.0f})"))
        elif percent >= warn_percent:
            found.append(Finding("warn", f"Koneksi puncak {percent:.0f}% dari max_connections ({peak:.0f}/{limit:.0f})"))
    if refused > 0:
        found.append(Finding("bad", f"{refused:.0f} koneksi ditolak karena max_connections penuh"))
    return found


def performance_findings(ratio: float | None, slow: float, deadlocks: float, *, min_ratio: int,
                         max_slow: int) -> list[Finding]:
    found = []
    if ratio is not None and ratio < min_ratio:
        found.append(Finding("warn", f"Hit ratio buffer pool {ratio:.1f}% (batas {min_ratio}%)"))
    if slow > max_slow:
        found.append(Finding("warn", f"{slow:.0f} slow query (batas {max_slow})"))
    if deadlocks > 0:
        found.append(Finding("warn", f"{deadlocks:.0f} deadlock"))
    return found


def disk_finding(days: float | None, bad_days: int, warn_days: int) -> Finding | None:
    if days is None or days >= warn_days:
        return None
    level = "bad" if days < bad_days else "warn"
    return Finding(level, f"Disk /backup diperkirakan penuh dalam {days:.0f} hari")


def backup_size_finding(latest: int | None, previous: list[int]) -> Finding | None:
    """Ukuran full terakhir dibanding rata-rata full sebelumnya; butuh minimal tiga pembanding."""
    if latest is None or len(previous) < 3:
        return None
    average = sum(previous) / len(previous)
    if average <= 0:
        return None
    ratio = latest / average
    if ratio < 0.5:
        return Finding("warn", f"Ukuran full terakhir hanya {ratio * 100:.0f}% dari rata-rata")
    if ratio > 2:
        return Finding("warn", f"Ukuran full terakhir {ratio:.1f}× rata-rata")
    return None
