"""Metrik kesehatan MariaDB untuk tahap 3. Dibaca lewat socket sebagai user DB `lumbung` (unix_socket, tanpa password)
yang hanya punya SHOW VIEW dan SELECT pada satu tabel performance_schema. Tidak ada hak baca isi tabel.
Semua fungsi mengembalikan None atau daftar kosong bila gagal: metrik tidak pernah menghalangi heartbeat."""
import os
import re
import subprocess

DB_SOCKET = "/run/mysqld/mysqld.sock"
DB_USER = "lumbung"
# Mode sidecar: MariaDB ada di container lain (namespace jaringan yang sama), jadi user `lumbung` masuk lewat TCP
# dengan password di berkas ini, milik user OS lumbung, izin 600. Isinya ditulis entrypoint.
SIDECAR_CNF = "/etc/lumbung/metrics.cnf"
SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")
SIZE_INTERVAL_SECONDS = 24 * 3600
MAX_DATABASES = 50
MAX_TABLES = 10
MAX_DIGESTS = 10
DIGEST_TEXT_LIMIT = 300
MAX_INDEX_TABLES = 20
# Slow query: pola yang pernah berjalan >= SLOW_MIN_SECONDS, dikirim tiap jam. LumbungDB menghitung selisih per jam dan
# menyaring lagi dengan batasnya sendiri, jadi nilai di sini hanya saringan kasar.
SLOW_INTERVAL_SECONDS = 3600
SLOW_MIN_SECONDS = 1
MAX_SLOW = 50
MAX_INDEX_ROWS = 100

# Penghitung kumulatif (selisihnya dihitung LumbungDB) dan nilai sesaat.
COUNTERS = (
    "Connection_errors_max_connections", "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads",
    "Slow_queries", "Innodb_deadlocks",
    # Grafik aktivitas per menit di halaman container.
    "Questions", "Connections", "Bytes_received", "Bytes_sent", "Com_select", "Com_insert", "Com_update", "Com_delete",
)
GAUGES = ("Threads_connected", "Threads_running", "Max_used_connections", "Uptime")

_STATUS_SQL = "SHOW GLOBAL STATUS WHERE Variable_name IN ({names})".format(
    names=",".join(f"'{n}'" for n in COUNTERS + GAUGES))
_VARIABLES_SQL = "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('max_connections','performance_schema')"
_SCHEMA_LIST = ",".join(f"'{s}'" for s in SYSTEM_SCHEMAS)
_SIZES_SQL = ("SELECT table_schema, table_name, COALESCE(data_length,0)+COALESCE(index_length,0) "
              f"FROM information_schema.TABLES WHERE table_schema NOT IN ({_SCHEMA_LIST}) AND table_type='BASE TABLE'")
# Penghitung per pola query tanpa EXPLAIN: EXPLAIN butuh hak baca tabel dan nilai literal, sedangkan penghitung ini sudah
# menunjukkan pemindaian penuh, join tanpa index, dan tabel sementara di disk.
_DIGEST_SQL = ("SELECT COALESCE(schema_name,''), digest_text, count_star, sum_timer_wait, sum_rows_examined, "
               "sum_rows_sent, sum_no_index_used, sum_no_good_index_used, sum_select_full_join, "
               "sum_created_tmp_disk_tables "
               "FROM performance_schema.events_statements_summary_by_digest "
               f"WHERE COALESCE(schema_name,'') NOT IN ({_SCHEMA_LIST}) AND digest_text IS NOT NULL "
               f"ORDER BY sum_timer_wait DESC LIMIT {MAX_DIGESTS}")

_SLOW_SQL = ("SELECT COALESCE(schema_name,''), digest, digest_text, count_star, sum_timer_wait, max_timer_wait, "
             "sum_rows_examined, sum_rows_sent, sum_no_index_used, sum_no_good_index_used, sum_select_full_join, "
             "sum_created_tmp_disk_tables "
             "FROM performance_schema.events_statements_summary_by_digest "
             f"WHERE COALESCE(schema_name,'') NOT IN ({_SCHEMA_LIST}) AND digest_text IS NOT NULL "
             f"AND digest IS NOT NULL AND max_timer_wait >= {SLOW_MIN_SECONDS * 10**12} "
             f"ORDER BY sum_timer_wait DESC LIMIT {MAX_SLOW}")
_SLOW_FIELDS = ("count", "total_ps", "max_ps", "rows_examined", "rows_sent", "no_index_used", "no_good_index_used",
                "full_join", "tmp_disk_tables")


def query(sql, runner=subprocess.run, timeout=20):
    """Baris tab-separated sebagai daftar daftar string; None bila gagal."""
    if os.environ.get("LUMBUNG_MODE") == "sidecar":
        login = [f"--defaults-extra-file={SIDECAR_CNF}", "--protocol=tcp"]
    else:
        login = ["--protocol=socket", f"--socket={DB_SOCKET}", f"--user={DB_USER}"]
    args = ["mariadb", *login, "--batch", "--skip-column-names", "--connect-timeout=5", "-e", sql]
    try:
        run = runner(args, capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if run.returncode != 0:
        return None
    return [line.split("\t") for line in run.stdout.splitlines() if line]


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect_sample(runner=subprocess.run):
    """Cuplikan tiap heartbeat. None bila MariaDB tidak terjangkau atau user `lumbung` belum dibuat."""
    status_rows = query(_STATUS_SQL, runner)
    variable_rows = query(_VARIABLES_SQL, runner)
    if not status_rows or variable_rows is None:
        return None
    status = {row[0]: _number(row[1]) for row in status_rows if len(row) == 2}
    variables = {row[0]: row[1] for row in variable_rows if len(row) == 2}
    max_connections = _number(variables.get("max_connections"))
    if status.get("Threads_connected") is None or max_connections is None:
        return None
    return {
        "status": {name: status[name] for name in COUNTERS + GAUGES if status.get(name) is not None},
        "max_connections": max_connections,
        "performance_schema": variables.get("performance_schema", "OFF").upper() in ("ON", "1"),
    }


# ---------- CPU dan memori proses mariadbd ----------
PROCESS_NAMES = ("mariadbd", "mysqld")


def _read(path):
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def find_server_pid(proc="/proc"):
    """PID mariadbd dari /proc/*/comm (berkas pid milik mysql tidak terbaca oleh user lumbung)."""
    try:
        entries = os.listdir(proc)
    except OSError:
        return None
    for entry in sorted(entries, key=lambda e: int(e) if e.isdigit() else 0):
        if entry.isdigit() and (_read(f"{proc}/{entry}/comm") or "").strip() in PROCESS_NAMES:
            return int(entry)
    return None


def _memory_limit(proc, cgroup):
    """Batas memori cgroup container (v2 lalu v1); tanpa batas: MemTotal mesin."""
    total = None
    for line in (_read(f"{proc}/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            total = _number(line.split()[1])
            total = total * 1024 if total else None
    for path in (f"{cgroup}/memory.max", f"{cgroup}/memory/memory.limit_in_bytes"):
        limit = _number((_read(path) or "").strip())
        if limit:
            return min(limit, total) if total else limit
    return total


def _cpu_limit(cgroup):
    """Jumlah core yang boleh dipakai: kuota cgroup (v2 lalu v1), cpuset, atau semua core."""
    quota = period = None
    parts = (_read(f"{cgroup}/cpu.max") or "").split()
    if len(parts) == 2:
        quota, period = _number(parts[0]), _number(parts[1])
    else:
        quota = _number((_read(f"{cgroup}/cpu/cpu.cfs_quota_us") or "").strip())
        period = _number((_read(f"{cgroup}/cpu/cpu.cfs_period_us") or "").strip())
    try:
        cores = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cores = os.cpu_count() or 1
    if quota and quota > 0 and period:
        return round(min(cores, quota / period), 2)
    return float(cores)


def collect_process(proc="/proc", cgroup="/sys/fs/cgroup"):
    """Waktu CPU kumulatif (detik) dan RSS mariadbd, beserta batas container. None bila proses tidak ditemukan."""
    pid = find_server_pid(proc)
    if pid is None:
        return None
    sidecar = os.environ.get("LUMBUNG_MODE") == "sidecar"
    stat = _read(f"{proc}/{pid}/stat") or ""
    # Nama proses di antara kurung bisa berisi spasi; field sesudah ')' dimulai dari state (field 3).
    fields = stat.rpartition(")")[2].split()
    rss = None
    for line in (_read(f"{proc}/{pid}/status") or "").splitlines():
        if line.startswith("VmRSS:"):
            rss = _number(line.split()[1])
    if len(fields) < 13 or rss is None:
        return None
    utime, stime = _number(fields[11]), _number(fields[12])
    if utime is None or stime is None:
        return None
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        ticks = 100
    return {
        "cpu_seconds": round((utime + stime) / ticks, 2),
        "rss_bytes": rss * 1024,
        # Sidecar: cgroup di sini milik sidecar, bukan container MariaDB, jadi batasnya tidak dilaporkan.
        "memory_limit": None if sidecar else _memory_limit(proc, cgroup),
        "cpus": None if sidecar else _cpu_limit(cgroup),
    }


def collect_sizes(runner=subprocess.run):
    """Ukuran tiap database dan tabel terbesar. Harian saja karena information_schema.TABLES berat."""
    rows = query(_SIZES_SQL, runner, timeout=120)
    if rows is None:
        return None
    databases, tables = {}, []
    for row in rows:
        if len(row) != 3 or _number(row[2]) is None:
            continue
        schema, table, size = row[0], row[1], int(row[2])
        databases[schema] = databases.get(schema, 0) + size
        tables.append((size, schema, table))
    tables.sort(reverse=True)
    top_databases = sorted(databases.items(), key=lambda kv: kv[1], reverse=True)[:MAX_DATABASES]
    return {
        "databases": [{"name": name, "bytes": size} for name, size in top_databases],
        "tables": [{"database": s, "table": t, "bytes": size} for size, s, t in tables[:MAX_TABLES]],
    }


def collect_digests(runner=subprocess.run):
    """Pola query terberat. Teks digest sudah tanpa nilai literal. Kosong bila performance_schema nonaktif."""
    rows = query(_DIGEST_SQL, runner, timeout=60)
    if rows is None:
        return None
    digests = []
    for row in rows:
        if len(row) not in (5, 10) or None in (_number(row[2]), _number(row[3]), _number(row[4])):
            continue
        item = {
            "database": row[0][:64], "text": row[1][:DIGEST_TEXT_LIMIT], "count": int(row[2]),
            "total_ps": int(row[3]), "rows_examined": int(row[4]),
        }
        if len(row) == 10:
            for key, value in zip(("rows_sent", "no_index_used", "no_good_index_used", "full_join", "tmp_disk_tables"),
                                  row[5:], strict=True):
                if _number(value) is not None:
                    item[key] = int(value)
        digests.append(item)
    return digests


def collect_slow(runner=subprocess.run):
    """Pola query yang pernah lambat, dengan penghitung kumulatif sejak MariaDB mulai. Teks digest tanpa nilai literal.
    None bila gagal dibaca."""
    rows = query(_SLOW_SQL, runner, timeout=60)
    if rows is None:
        return None
    slow = []
    for row in rows:
        if len(row) != 12:
            continue
        numbers = [_number(value) for value in row[3:]]
        if None in numbers or not row[1]:
            continue
        slow.append({"database": row[0][:64], "digest": row[1][:64], "text": row[2][:DIGEST_TEXT_LIMIT],
                     **dict(zip(_SLOW_FIELDS, numbers, strict=True))})
    return slow


_TABLE_REF = re.compile(r"\b(?:from|join|update|into)\s+`?(\w+)`?(?:\s*\.\s*`?(\w+)`?)?", re.IGNORECASE)
_STATISTICS_SQL = ("SELECT table_schema, table_name, index_name, non_unique, "
                   "GROUP_CONCAT(column_name ORDER BY seq_in_index SEPARATOR ',') "
                   "FROM information_schema.STATISTICS WHERE {where} "
                   "GROUP BY table_schema, table_name, index_name, non_unique LIMIT " + str(MAX_INDEX_ROWS))


def referenced_tables(digests):
    """(database, tabel) yang disebut digest. Hanya nama; digest sudah tanpa nilai literal."""
    found = []
    for item in digests:
        for first, second in _TABLE_REF.findall(item.get("text", "")):
            pair = (first, second) if second else (item.get("database") or "", first)
            if pair[0] and pair[0] not in SYSTEM_SCHEMAS and pair not in found:
                found.append(pair)
    return found[:MAX_INDEX_TABLES]


def _quote(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def collect_indexes(digests, runner=subprocess.run):
    """Index tabel yang disebut pola query terberat, dari information_schema.STATISTICS (cukup SHOW VIEW). Nama saja."""
    pairs = referenced_tables(digests)
    if not pairs:
        return []
    where = " OR ".join(f"(table_schema={_quote(db)} AND table_name={_quote(table)})" for db, table in pairs)
    rows = query(_STATISTICS_SQL.format(where=where), runner, timeout=60)
    if rows is None:
        return None
    indexes = []
    for row in rows:
        if len(row) != 5 or _number(row[3]) is None:
            continue
        indexes.append({"database": row[0][:64], "table": row[1][:64], "index": row[2][:64], "unique": row[3] == "0",
                        "columns": [c[:64] for c in row[4].split(",")][:16]})
    return indexes


class Collector:
    """Menyimpan kapan ukuran terakhir diambil; ukuran dan digest ikut heartbeat hanya sekali sehari."""

    def __init__(self, clock, runner=subprocess.run, process=collect_process):
        self.clock = clock
        self.runner = runner
        self.process = process
        self.last_sizes_at = None
        self.last_slow_at = None

    def payload(self):
        sample = collect_sample(self.runner)
        if sample is None:
            return None
        try:
            process = self.process()
        except Exception:    # noqa: BLE001 - metrik proses tidak boleh menghalangi heartbeat
            process = None
        if process is not None:
            sample["process"] = process
        now = self.clock()
        if self.last_sizes_at is None or now - self.last_sizes_at >= SIZE_INTERVAL_SECONDS:
            sizes = collect_sizes(self.runner)
            if sizes is not None:
                sample["sizes"] = sizes
                if sample["performance_schema"]:
                    digests = collect_digests(self.runner)
                    if digests is not None:
                        sample["digests"] = digests
                        indexes = collect_indexes(digests, self.runner) if digests else []
                        if indexes is not None:
                            sample["indexes"] = indexes
                self.last_sizes_at = now
        if sample["performance_schema"] and (
                self.last_slow_at is None or now - self.last_slow_at >= SLOW_INTERVAL_SECONDS):
            slow = collect_slow(self.runner)
            if slow is not None:
                sample["slow"] = slow
                self.last_slow_at = now
        return sample
