"""Tuning MariaDB dari memori, CPU, jumlah koneksi, dan jenis disk. Dipakai tuning.sh tiap container start, lumbung-helper
saat LumbungDB mengirim tuning baru, dan agen yang melaporkan hasilnya.

Masukan hanya empat nilai (mode, memori, koneksi, disk); parameter MariaDB dihitung di sini, bukan dikirim LumbungDB.
Berkas ini milik root di dalam image. Hanya library bawaan Python.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

TUNING_FILE = "/backup/.lumbung/tuning.json"
EFFECTIVE_FILE = "/run/lumbung/tuning-effective.json"
CNF_FILE = "/etc/mysql/conf.d/zz-tuning.cnf"
SOCKET = "/run/mysqld/mysqld.sock"
# Akun yang hanya boleh memanggil mysql.lumbung_set_global (lihat scripts/tuning-account.sql)
TUNING_USER = "lumbung_tuning"
MB = 1024 * 1024

MODES = ("auto", "off")
DISKS = ("ssd", "hdd")
MIN_MEMORY_MB = 512
MAX_MEMORY_MB = 4 * 1024 * 1024
MIN_CONNECTIONS = 10
MAX_CONNECTIONS = 10000
DEFAULT_CONNECTIONS = 200

# Urutan di zz-tuning.cnf. Semua bisa diubah tanpa restart kecuali innodb_log_buffer_size.
VARIABLES = (
    "max_connections", "thread_cache_size",
    "innodb_buffer_pool_size", "innodb_log_file_size", "innodb_log_buffer_size",
    "innodb_read_io_threads", "innodb_write_io_threads", "innodb_io_capacity", "innodb_io_capacity_max",
    "innodb_flush_neighbors",
    "tmp_table_size", "max_heap_table_size", "table_open_cache", "table_definition_cache",
)
SIZES = {"innodb_buffer_pool_size", "innodb_log_file_size", "innodb_log_buffer_size", "tmp_table_size",
         "max_heap_table_size"}
RESTART_ONLY = {"innodb_log_buffer_size"}


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def inputs_error(inputs):
    """Pesan error untuk masukan yang tidak valid, atau None. memory_mb 0 = dari limit container."""
    if not isinstance(inputs, dict):
        return "tuning bukan objek"
    if inputs.get("mode") not in MODES:
        return "mode tuning tidak valid"
    memory = _int(inputs.get("memory_mb"))
    if memory is None or (memory != 0 and not MIN_MEMORY_MB <= memory <= MAX_MEMORY_MB):
        return f"memori harus 0 (dari limit container) atau {MIN_MEMORY_MB}-{MAX_MEMORY_MB} MB"
    connections = _int(inputs.get("max_connections"))
    if connections is None or not MIN_CONNECTIONS <= connections <= MAX_CONNECTIONS:
        return f"max_connections harus {MIN_CONNECTIONS}-{MAX_CONNECTIONS}"
    if inputs.get("disk") not in DISKS:
        return "jenis disk harus ssd atau hdd"
    return None


def _clamp(value, low, high):
    return max(low, min(high, value))


def _read(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def read_limits(root=""):
    """Limit memori dan CPU container dari cgroup v2, lalu v1. Tanpa limit, memory_mb None."""
    host_mb = None
    for line in (_read(f"{root}/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            host_mb = int(line.split()[1]) // 1024
    limit_mb = None
    raw = _read(f"{root}/sys/fs/cgroup/memory.max") or _read(f"{root}/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if raw and raw.isdigit():
        limit_mb = int(raw) // MB
        # "Tanpa limit" di v1 berupa angka sangat besar
        if host_mb and limit_mb >= host_mb:
            limit_mb = None
    cpus = os.cpu_count() or 1
    quota = period = None
    cpu_max = _read(f"{root}/sys/fs/cgroup/cpu.max")
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            quota, period = int(parts[0]), int(parts[1])
    else:
        q, p = _read(f"{root}/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read(f"{root}/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q and p and q.isdigit() and p.isdigit():
            quota, period = int(q), int(p)
    if quota and period:
        cpus = min(cpus, -(-quota // period))
    return {"memory_mb": limit_mb, "host_memory_mb": host_mb, "cpus": cpus}


def compute(inputs, limits):
    """Parameter MariaDB (byte atau jumlah) dan asal memori. None untuk mode off."""
    if inputs["mode"] == "off":
        return None, None
    limit_mb, host_mb = limits.get("memory_mb"), limits.get("host_memory_mb") or 1024
    if inputs["memory_mb"]:
        memory_mb, source = inputs["memory_mb"], "diatur"
        if limit_mb and memory_mb > limit_mb:
            memory_mb, source = limit_mb, "limit container"
    elif limit_mb:
        memory_mb, source = limit_mb, "limit container"
    else:
        # Tanpa limit, RAM yang terbaca adalah RAM seluruh host yang mungkin dipakai container lain
        memory_mb, source = host_mb // 2, "50% RAM host"
    connections, cpus = inputs["max_connections"], limits.get("cpus") or 1

    # Buffer pool: porsi terbesar. Sisanya untuk overhead server, tabel sementara, dan buffer per koneksi
    # (sort/join/read buffer + stack thread, dihitung ±2MB per koneksi)
    pool_pct = 50 if memory_mb < 2048 else 65 if memory_mb < 8192 else 75
    reserve = _clamp(memory_mb // 10, 256, 2048)
    pool = min(memory_mb * pool_pct // 100, memory_mb - reserve - connections * 2)
    pool = max(128, pool // 128 * 128)          # kelipatan 128M, minimal 128M
    # Redo log ±25% buffer pool: checkpoint tidak terlalu sering, recovery setelah crash tetap cepat
    redo = _clamp(pool // 4, 96, 4096)
    tmp = _clamp(memory_mb // 64, 16, 256)
    table_cache = _clamp(connections * 10, 2000, 10000)
    io_threads = _clamp(cpus, 4, 16)
    ssd = inputs["disk"] == "ssd"
    values = {
        "max_connections": connections,
        "thread_cache_size": _clamp(connections // 4, 16, 256),
        "innodb_buffer_pool_size": pool * MB,
        "innodb_log_file_size": redo * MB,
        "innodb_log_buffer_size": (64 if pool >= 4096 else 16) * MB,
        "innodb_read_io_threads": io_threads,
        "innodb_write_io_threads": io_threads,
        "innodb_io_capacity": 2000 if ssd else 200,
        "innodb_io_capacity_max": 4000 if ssd else 400,
        "innodb_flush_neighbors": 0 if ssd else 1,
        "tmp_table_size": tmp * MB,
        "max_heap_table_size": tmp * MB,
        "table_open_cache": table_cache,
        "table_definition_cache": table_cache,
    }
    return values, {"memory_mb": memory_mb, "source": source, "cpus": cpus}


def render_cnf(values, basis, inputs):
    lines = [
        "# Dibuat otomatis oleh /opt/lumbung/tuning.py, jangan diedit (akan ditimpa)",
        f"# Memori: {basis['memory_mb']}M ({basis['source']}), CPU: {basis['cpus']}, disk: {inputs['disk']}",
        "[mysqld]",
    ]
    for name in VARIABLES:
        value = values[name]
        lines.append(f"{name} = {value // MB}M" if name in SIZES else f"{name} = {value}")
    return "\n".join(lines) + "\n"


def write_atomic(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _memory_from_env(value):
    match = re.fullmatch(r"([0-9]+)([GM]?)", (value or "").upper())
    if not match:
        return None
    return int(match.group(1)) * (1024 if match.group(2) == "G" else 1)


def from_env(env):
    """Masukan dari DB_TUNING, DB_MEMORY, DB_MAX_CONNECTIONS, DB_DISK. Nilai tidak valid diberi peringatan."""
    inputs = {"mode": "off" if env.get("DB_TUNING") == "off" else "auto", "memory_mb": 0,
              "max_connections": DEFAULT_CONNECTIONS, "disk": "ssd"}
    if env.get("DB_MEMORY"):
        memory = _memory_from_env(env["DB_MEMORY"])
        if memory is None or memory < MIN_MEMORY_MB:
            print(f"DB_MEMORY tidak valid: '{env['DB_MEMORY']}' (contoh: 4G atau 4096M, minimal 512M), "
                  "tuning dilewati", file=sys.stderr)
            inputs["mode"] = "off"
        else:
            inputs["memory_mb"] = min(memory, MAX_MEMORY_MB)
    connections = env.get("DB_MAX_CONNECTIONS") or ""
    if connections:
        if connections.isdigit() and MIN_CONNECTIONS <= int(connections) <= MAX_CONNECTIONS:
            inputs["max_connections"] = int(connections)
        else:
            print(f"DB_MAX_CONNECTIONS tidak valid: '{connections}', dipakai {DEFAULT_CONNECTIONS}", file=sys.stderr)
    disk = env.get("DB_DISK") or "ssd"
    if disk in DISKS:
        inputs["disk"] = disk
    else:
        print(f"DB_DISK tidak valid: '{disk}' (ssd/hdd), dipakai ssd", file=sys.stderr)
    return inputs


def read_saved():
    """Tuning dari LumbungDB yang tersimpan. None bila tidak ada atau tidak valid: isinya dipakai sebagai root."""
    try:
        with open(TUNING_FILE) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if inputs_error(data) or _int(data.get("version")) is None:
        return None
    return data


def _effective(source, version, inputs, values, basis, limits, live_note=None):
    return {
        "source": source, "version": version,
        **{key: inputs[key] for key in ("mode", "memory_mb", "max_connections", "disk")},
        "values": values, "basis": basis, "limits": limits, "live_note": live_note,
    }


def _write_cnf(values, basis, inputs):
    if values is None:
        try:
            os.unlink(CNF_FILE)
        except FileNotFoundError:
            pass
    else:
        write_atomic(CNF_FILE, render_cnf(values, basis, inputs))


def install(env):
    """Dipanggil tuning.sh tiap container start, sebagai root, sebelum MariaDB berjalan."""
    saved = read_saved()
    if saved:
        source, version, inputs = "lumbung", saved["version"], saved
    else:
        source, version, inputs = ("env" if any(env.get(k) for k in ("DB_TUNING", "DB_MEMORY", "DB_MAX_CONNECTIONS",
                                                                       "DB_DISK")) else "bawaan"), 0, from_env(env)
    limits = read_limits()
    values, basis = compute(inputs, limits)
    _write_cnf(values, basis, inputs)
    write_atomic(EFFECTIVE_FILE, json.dumps(_effective(source, version, inputs, values, basis, limits)))
    if values is None:
        print("Tuning otomatis nonaktif")
        return
    if basis["source"] == "50% RAM host":
        print(f"PERINGATAN: container tanpa limit memori dan memori tuning kosong, dipakai 50% RAM host "
              f"({basis['memory_mb']}M)", file=sys.stderr)
    print(f"Tuning MariaDB ({source}): memori {basis['memory_mb']}M ({basis['source']}), CPU {basis['cpus']}, "
          f"disk {inputs['disk']}")
    print(f"  buffer pool {values['innodb_buffer_pool_size'] // MB}M, redo log {values['innodb_log_file_size'] // MB}M, "
          f"max_connections {values['max_connections']}, tmp table {values['tmp_table_size'] // MB}M")


def _set_live(values, runner=subprocess.run):
    """SET GLOBAL lewat procedure yang hanya menerima variabel tuning. Pesan bila sebagian atau semua gagal."""
    failed = []
    for name in VARIABLES:
        if name in RESTART_ONLY:
            continue
        args = ["gosu", TUNING_USER, "mariadb", "--protocol=socket", f"--socket={SOCKET}", f"--user={TUNING_USER}",
                "--connect-timeout=5", "-e", f"CALL mysql.lumbung_set_global('{name}', {int(values[name])})"]
        try:
            run = runner(args, capture_output=True, text=True, errors="replace", timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"tidak bisa diterapkan langsung ({type(exc).__name__}); berlaku setelah restart"
        if run.returncode != 0:
            message = (run.stderr or "").strip().splitlines()[-1:] or [f"kode {run.returncode}"]
            if "Access denied" in message[0] or "does not exist" in message[0]:
                return "akun lumbung_tuning belum ada; berlaku setelah restart (lihat README)"
            failed.append(name)
    if failed:
        return "berlaku setelah restart: " + ", ".join(failed)
    return None


def apply(version, inputs, runner=subprocess.run):
    """Dipanggil helper. Disimpan dulu (berlaku saat start berikutnya), lalu diterapkan ke MariaDB yang berjalan."""
    limits = read_limits()
    values, basis = compute(inputs, limits)
    saved = {"version": version, **{key: inputs[key] for key in ("mode", "memory_mb", "max_connections", "disk")}}
    # tuning.json dulu: saat start, zz-tuning.cnf selalu dihitung ulang dari berkas ini
    write_atomic(TUNING_FILE, json.dumps(saved))
    _write_cnf(values, basis, inputs)
    if values is None:
        note = "mode off berlaku setelah restart"
    else:
        note = _set_live(values, runner)
    write_atomic(EFFECTIVE_FILE, json.dumps(_effective("lumbung", version, inputs, values, basis, limits, note)))
    return note


if __name__ == "__main__":
    if sys.argv[1:] != ["install"]:
        sys.exit("pemakaian: tuning.py install")
    install(os.environ)
