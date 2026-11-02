"""Validasi dan penerapan jadwal backup. Dipakai agen, lumbung-helper, dan install_cron.sh.

Berkas ini milik root di dalam image. Hanya library bawaan Python.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

SCHEDULE_FILE = "/backup/.lumbung/schedule.json"
EFFECTIVE_FILE = "/run/lumbung/schedule-effective.json"
LOG_DIR = "/backup/log"
SCRIPTS = {
    "full": ("/opt/backup/databasebackup.sh", "database_backup_cron.log"),
    "dump": ("/opt/backup/databasedump.sh", "database_dump_cron.log"),
    "binlog": ("/opt/backup/binlogbackup.sh", "binlog_backup_cron.log"),
}
KINDS = ("full", "dump", "binlog")
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_PART = re.compile(r"^(\*|\d+(?:-\d+)?)(?:/(\d+))?$")


def _field_valid(field, low, high):
    for part in field.split(","):
        match = _PART.match(part)
        if not match:
            return False
        step = int(match.group(2) or 1)
        if step < 1:
            return False
        if match.group(1) != "*":
            bounds = match.group(1).split("-")
            first, last = int(bounds[0]), int(bounds[-1])
            if first < low or last > high or first > last:
                return False
    return True


def cron_valid(expr):
    """Lima kolom cron dengan karakter 0-9 * / , -, atau off. Selain itu ditolak."""
    if not isinstance(expr, str):
        return False
    if expr == "off":
        return True
    if not re.fullmatch(r"[0-9*/,\- ]+", expr):
        return False
    fields = expr.split(" ")
    if len(fields) != 5:
        return False
    return all(_field_valid(f, low, high) for f, (low, high) in zip(fields, _RANGES))


def schedule_error(schedule):
    """Pesan error untuk jadwal yang tidak valid, atau None bila valid."""
    if not isinstance(schedule, dict):
        return "jadwal bukan objek"
    for kind in KINDS:
        if not cron_valid(schedule.get(kind)):
            return f"jadwal {kind} tidak valid"
    return None


def render_crontab(schedule):
    lines = []
    for kind in KINDS:
        expr = schedule[kind]
        if expr == "off":
            continue
        script, log_name = SCRIPTS[kind]
        # stdout sudah tercatat di log script; di sini cukup stderr supaya isi log tidak dobel
        lines.append(f"{expr} {script} > /dev/null 2>> {LOG_DIR}/{log_name}\n")
    return "".join(lines)


def write_crontab(schedule):
    subprocess.run(["crontab", "-"], input=render_crontab(schedule), text=True, check=True, capture_output=True)


def write_json_atomic(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
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


def read_saved(path=SCHEDULE_FILE):
    """Jadwal dari LumbungDB yang tersimpan. None bila tidak ada atau tidak valid: isinya dipakai sebagai root."""
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if schedule_error(data) or not isinstance(data.get("version"), int) or isinstance(data["version"], bool):
        return None
    return data


def resolve(saved, env_values, defaults):
    """Urutan jadwal: schedule.json, lalu env, lalu bawaan dari nama container."""
    if saved:
        return {"source": "lumbung", "version": saved["version"], **{k: saved[k] for k in KINDS}}
    result = {"source": "bawaan", "version": 0}
    for kind in KINDS:
        value = env_values.get(kind) or ""
        if value and cron_valid(value):
            result[kind] = value
            result["source"] = "env"
        else:
            if value:
                print(f"Jadwal {kind} dari env tidak valid, dipakai bawaan", file=sys.stderr)
            result[kind] = defaults[kind]
    return result


def install(env_values, defaults):
    """Dipanggil install_cron.sh tiap container start, sebagai root."""
    effective = resolve(read_saved(), env_values, defaults)
    write_crontab(effective)
    write_json_atomic(EFFECTIVE_FILE, effective)
    return effective


def apply_schedule(version, schedule):
    """Dipanggil helper. Bila gagal, crontab lama tetap dipakai dan berkas tidak berubah."""
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    saved = {"version": version, **{k: schedule[k] for k in KINDS}}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SCHEDULE_FILE), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(saved, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        write_crontab(saved)
        os.replace(tmp, SCHEDULE_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    write_json_atomic(EFFECTIVE_FILE, {"source": "lumbung", **saved})


if __name__ == "__main__":
    # install <env_full> <env_dump> <env_binlog> <bawaan_full> <bawaan_dump> <bawaan_binlog>
    if len(sys.argv) != 8 or sys.argv[1] != "install":
        sys.exit("pemakaian: schedule.py install ENV_FULL ENV_DUMP ENV_BINLOG DEF_FULL DEF_DUMP DEF_BINLOG")
    env = dict(zip(KINDS, sys.argv[2:5]))
    default = dict(zip(KINDS, sys.argv[5:8]))
    applied = install(env, default)
    print(f"Jadwal backup ({applied['source']}):")
    print(render_crontab(applied), end="")
