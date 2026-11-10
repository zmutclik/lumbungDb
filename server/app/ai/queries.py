"""Bahan analisis slow query: sinyal dari penghitung performance_schema dan index tabel yang disebut query. Tanpa EXPLAIN:
EXPLAIN butuh hak baca tabel dan nilai literal, sedangkan sinyal di sini dihitung aturan biasa dari angka agregat. Hasil
analisis hanya saran yang belum terverifikasi, dan model diminta menyebutnya."""
import re

from app.ai.normalize import normalize_query

_TABLE_REF = re.compile(r"\b(?:from|join|update|into)\s+`?(\w+)`?(?:\s*\.\s*`?(\w+)`?)?", re.IGNORECASE)
RATIO_WARN = 100          # baris diperiksa per baris dikirim
MAX_QUERIES = 5


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def tables_in(text: str, database: str) -> set[tuple[str, str]]:
    found = set()
    for first, second in _TABLE_REF.findall(text):
        found.add((first, second) if second else (database, first))
    return found


def signals(digest: dict) -> list[str]:
    """Temuan aturan biasa atas satu pola query, dari angka agregat saja."""
    count = _number(digest.get("count")) or 0
    out = []
    if (no_index := _number(digest.get("no_index_used"))) and count:
        out.append(f"{no_index} dari {count} eksekusi tanpa index sama sekali (pemindaian penuh)")
    elif (no_good := _number(digest.get("no_good_index_used"))) and count:
        out.append(f"{no_good} dari {count} eksekusi memakai index yang kurang baik")
    if _number(digest.get("full_join")):
        out.append(f"{digest['full_join']} join tanpa index pada tabel yang digabung")
    if _number(digest.get("tmp_disk_tables")):
        out.append(f"{digest['tmp_disk_tables']} tabel sementara ditulis ke disk")
    examined, sent = _number(digest.get("rows_examined")), _number(digest.get("rows_sent"))
    if examined and sent is not None and examined / max(sent, 1) >= RATIO_WARN:
        out.append(f"memeriksa {examined // max(sent, 1)} baris untuk tiap baris yang dikirim")
    return out


def entries(metrics: dict | None, *, limit: int = MAX_QUERIES) -> list[dict]:
    """Pola query terberat dari heartbeat terakhir, dinormalisasi lagi (data agen tidak dipercaya), beserta sinyal dan
    index tabelnya."""
    metrics = metrics or {}
    index_rows = [r for r in (metrics.get("indexes") or []) if isinstance(r, dict)]
    out = []
    for raw in (metrics.get("digests") or [])[:limit]:
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            continue
        database = str(raw.get("database") or "")[:64]
        query = normalize_query(raw["text"])
        tables = tables_in(query, database)
        indexes: dict[str, list[str]] = {}
        for row in index_rows:
            if (str(row.get("database")), str(row.get("table"))) in tables and isinstance(row.get("columns"), list):
                columns = ",".join(str(c)[:64] for c in row["columns"][:16])
                label = f"{str(row.get('index'))[:64]}({columns}){' unik' if row.get('unique') else ''}"
                indexes.setdefault(f"{row.get('database')}.{row.get('table')}"[:129], []).append(label)
        out.append({
            "database": database, "query": query, "count": _number(raw.get("count")),
            "seconds": round((_number(raw.get("total_ps")) or 0) / 1e12, 3),
            "rows_examined": _number(raw.get("rows_examined")), "rows_sent": _number(raw.get("rows_sent")),
            "sinyal": signals(raw), "indeks_tabel": indexes,
        })
    return out
