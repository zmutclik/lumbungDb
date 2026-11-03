"""Metrik kesehatan MariaDB untuk tahap 3. Dibaca lewat socket sebagai user DB `lumbung` (unix_socket, tanpa password)
yang hanya punya SHOW VIEW dan SELECT pada satu tabel performance_schema. Tidak ada hak baca isi tabel.
Semua fungsi mengembalikan None atau daftar kosong bila gagal: metrik tidak pernah menghalangi heartbeat."""
import subprocess

DB_SOCKET = "/run/mysqld/mysqld.sock"
DB_USER = "lumbung"
SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")
SIZE_INTERVAL_SECONDS = 24 * 3600
MAX_DATABASES = 50
MAX_TABLES = 10
MAX_DIGESTS = 10
DIGEST_TEXT_LIMIT = 300

# Penghitung kumulatif (selisihnya dihitung LumbungDB) dan nilai sesaat.
COUNTERS = (
    "Connection_errors_max_connections", "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads",
    "Slow_queries", "Innodb_deadlocks",
)
GAUGES = ("Threads_connected", "Threads_running", "Max_used_connections", "Uptime")

_STATUS_SQL = "SHOW GLOBAL STATUS WHERE Variable_name IN ({names})".format(
    names=",".join(f"'{n}'" for n in COUNTERS + GAUGES))
_VARIABLES_SQL = "SHOW GLOBAL VARIABLES WHERE Variable_name IN ('max_connections','performance_schema')"
_SCHEMA_LIST = ",".join(f"'{s}'" for s in SYSTEM_SCHEMAS)
_SIZES_SQL = ("SELECT table_schema, table_name, COALESCE(data_length,0)+COALESCE(index_length,0) "
              f"FROM information_schema.TABLES WHERE table_schema NOT IN ({_SCHEMA_LIST}) AND table_type='BASE TABLE'")
_DIGEST_SQL = ("SELECT COALESCE(schema_name,''), digest_text, count_star, sum_timer_wait, sum_rows_examined "
               "FROM performance_schema.events_statements_summary_by_digest "
               f"WHERE COALESCE(schema_name,'') NOT IN ({_SCHEMA_LIST}) AND digest_text IS NOT NULL "
               f"ORDER BY sum_timer_wait DESC LIMIT {MAX_DIGESTS}")


def query(sql, runner=subprocess.run, timeout=20):
    """Baris tab-separated sebagai daftar daftar string; None bila gagal."""
    args = ["mariadb", "--protocol=socket", f"--socket={DB_SOCKET}", f"--user={DB_USER}", "--batch",
            "--skip-column-names", "--connect-timeout=5", "-e", sql]
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
        if len(row) != 5 or None in (_number(row[2]), _number(row[3]), _number(row[4])):
            continue
        digests.append({
            "database": row[0][:64], "text": row[1][:DIGEST_TEXT_LIMIT], "count": int(row[2]),
            "total_ps": int(row[3]), "rows_examined": int(row[4]),
        })
    return digests


class Collector:
    """Menyimpan kapan ukuran terakhir diambil; ukuran dan digest ikut heartbeat hanya sekali sehari."""

    def __init__(self, clock, runner=subprocess.run):
        self.clock = clock
        self.runner = runner
        self.last_sizes_at = None

    def payload(self):
        sample = collect_sample(self.runner)
        if sample is None:
            return None
        now = self.clock()
        if self.last_sizes_at is None or now - self.last_sizes_at >= SIZE_INTERVAL_SECONDS:
            sizes = collect_sizes(self.runner)
            if sizes is not None:
                sample["sizes"] = sizes
                if sample["performance_schema"]:
                    digests = collect_digests(self.runner)
                    if digests is not None:
                        sample["digests"] = digests
                self.last_sizes_at = now
        return sample
