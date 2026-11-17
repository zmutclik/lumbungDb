"""Runner LumbungDB: mengambil tugas uji restore, mengunduh file lewat HTTPS, memulihkannya di container sementara,
lalu melaporkan hasil. Hanya library bawaan Python. Semua alat (age, mbstream, mariadb-backup, mariadb) dijalankan di
dalam image MariaDB lewat `docker run`, jadi mesin runner hanya butuh Docker.

Hasil tugas ada tiga. "Gagal" berarti backup-nya yang bermasalah (file rusak, prepare gagal, tabel tidak terbaca).
"Tidak terlaksana" berarti runner yang bermasalah (unduhan putus, Docker error, scratch penuh): backup tidak dicap gagal."""
import argparse
import contextlib
import glob
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse

SERVER_DIR = "/lumbung/server"
HTTP_TIMEOUT = 60
CHUNK = 1024 * 1024
LABEL = "lumbung.runner=1"
VERSION_FILE = "/opt/lumbung/VERSION"
SOCKET = "/run/mysqld/mysqld.sock"
SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")
MAIN_TABLES = 5
LOG_LIMIT = 4000


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


class NotRun(Exception):
    """Runner yang bermasalah, bukan backup-nya."""

    def __init__(self, step, message=""):
        super().__init__(message or step)
        self.step, self.message = step, message or step


class Failed(NotRun):
    """Backup-nya yang bermasalah."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Client:
    def __init__(self, base_url, token):
        parsed = urllib.parse.urlsplit(base_url)
        self.scheme, self.host, self.port = parsed.scheme, parsed.hostname, parsed.port
        self.prefix = parsed.path.rstrip("/")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _connect(self):
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=HTTP_TIMEOUT, context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port, timeout=HTTP_TIMEOUT)

    def request(self, method, path, body=None, headers=None):
        conn = self._connect()
        data = json.dumps(body).encode() if body is not None else None
        merged = {"X-Runner-Token": self.token, **({"Content-Type": "application/json"} if data else {}), **(headers or {})}
        conn.request(method, self.prefix + path, body=data, headers=merged)
        return conn, conn.getresponse()

    def json(self, method, path, body=None):
        conn, response = self.request(method, path, body)
        try:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
        except ValueError:
            return response.status, {}
        finally:
            conn.close()

    def download(self, job_id, entry, dest):
        """Unduhan dilanjutkan dari byte terakhir bila terputus, dan checksum file terenkripsi diperiksa."""
        url = f"/api/runner/jobs/{job_id}/files/{entry['id']}"
        attempts = 0
        while True:
            have = os.path.getsize(dest) if os.path.exists(dest) else 0
            if have > entry["size"]:
                os.unlink(dest)
                have = 0
            if have < entry["size"]:
                try:
                    self._fetch(url, dest, have)
                except (OSError, http.client.HTTPException) as exc:
                    attempts += 1
                    if attempts >= 5:
                        raise NotRun(f"unduh {entry['relpath']}", f"unduhan gagal: {type(exc).__name__}") from exc
                    time.sleep(min(2 ** attempts, 30))
                    continue
            if os.path.getsize(dest) == entry["size"]:
                break
        if sha256_file(dest) != entry["sha256"]:
            os.unlink(dest)
            raise Failed(f"checksum {entry['relpath']}", "SHA256 file berbeda dengan metadata: file rusak")

    def _fetch(self, url, dest, offset):
        conn, response = self.request("GET", url, headers={"Range": f"bytes={offset}-"} if offset else None)
        try:
            if response.status == 503:
                raise NotRun("unduh", "LumbungDB menolak unduhan (NAS bermasalah atau penuh)")
            if response.status not in (200, 206):
                raise NotRun("unduh", f"HTTP {response.status}")
            with open(dest, "ab" if offset and response.status == 206 else "wb") as handle:
                for chunk in iter(lambda: response.read(CHUNK), b""):
                    handle.write(chunk)
        finally:
            conn.close()


def run(args, timeout=3600, stdin_path=None):
    """Satu-satunya tempat memanggil proses luar, supaya tes cukup mengganti fungsi ini."""
    stdin = open(stdin_path, "rb") if stdin_path else None
    try:
        done = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, stdin=stdin)
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"melebihi {timeout} detik: {exc}"
    except OSError as exc:
        return 127, "", str(exc)
    finally:
        if stdin:
            stdin.close()
    return done.returncode, done.stdout, done.stderr


def purge_dir(path, config, runner=run):
    """Isi folder kerja dimiliki mysql/root (hasil prepare), jadi dihapus dari dalam container, baru foldernya."""
    if not os.path.isdir(path):
        return
    runner(["docker", "run", "--rm", "--label", LABEL, "--network", "none", "--entrypoint", "bash", "-v", f"{path}:/work",
            config["image"], "-c", "find /work -mindepth 1 -delete"], 600)
    shutil.rmtree(path, ignore_errors=True)
    if os.path.isdir(path):
        # Folder kosong yang tertutup (mis. chmod 750 oleh entrypoint image) tidak bisa dibuka rmtree dari user host.
        parent, name = os.path.split(path.rstrip("/"))
        runner(["docker", "run", "--rm", "--label", LABEL, "--network", "none", "--entrypoint", "bash", "-v",
                f"{parent}:/parent", config["image"], "-c", f"rmdir -- /parent/{name}"], 600)


class Restorer:
    """Satu tugas uji. Semua container dan folder kerjanya dibersihkan di akhir, juga saat gagal."""

    def __init__(self, config, client, job, runner=run, progress=None):
        self.config, self.client, self.job, self.run = config, client, job, runner
        self.progress = progress or (lambda step: None)
        self.work = os.path.join(config["scratch_dir"], f"job-{job['id']}")
        self.containers = []
        self.table_counts = {}      # database → jumlah tabel di hasil restore full
        self.full_ok = False

    # ---------- alat ----------
    def _tool(self, script, timeout=7200):
        key = self.config["age_identity"]
        return self.run(["docker", "run", "--rm", "--label", LABEL, "--network", "none", "--entrypoint", "bash",
                         "-v", f"{self.work}:/work", "-v", f"{key}:/keys/age.key:ro", self.config["image"],
                         "-c", script], timeout)

    def _start(self, name, extra):
        full = f"lumbung-runner-{self.job['id']}-{name}"
        code, _, err = self.run(["docker", "run", "-d", "--name", full, "--label", LABEL, "--network", "none", *extra])
        if code != 0:
            raise NotRun(f"jalankan container {name}", err.strip()[-300:])
        self.containers.append(full)
        return full

    def _sql(self, container, sql, password=None, stdin_path=None):
        """Satu pernyataan lewat -e, atau seluruh isi dump lewat stdin."""
        args = ["docker", "exec", *(["-i"] if stdin_path else []), *(["-e", f"MYSQL_PWD={password}"] if password else []),
                container, "mariadb", "-S", SOCKET, "-uroot", "-BN"]
        if not stdin_path:
            args += ["-e", sql]
        return self.run(args, 3600, stdin_path)

    def _wait_ready(self, container, password=None, init=False):
        """init=True untuk container kosong: server sementara milik entrypoint juga menjawab, jadi tunggu sampai
        inisialisasi selesai dulu."""
        for _ in range(self.config["ready_seconds"]):
            if init:
                _, logs, err = self.run(["docker", "logs", container])
                ready = "init process done" in logs + err
            else:
                ready = True
            code, out, _ = self._sql(container, "SELECT 1", password) if ready else (1, "", "")
            if code == 0 and out.strip() == "1":
                return
            time.sleep(1)
        _, logs, err = self.run(["docker", "logs", "--tail", "15", container])
        raise Failed("MariaDB tidak naik", (logs + err).strip()[-LOG_LIMIT:])

    def _decrypt(self, src, dest_in_work, gunzip=True):
        rel_src = os.path.relpath(src, self.work)
        rel_dest = os.path.relpath(dest_in_work, self.work)
        pipe = "| zcat " if gunzip else ""
        code, _, err = self._tool(f"set -eo pipefail; age -d -i /keys/age.key /work/{rel_src} {pipe}> /work/{rel_dest}")
        if code != 0:
            raise Failed("buka enkripsi", err.strip()[-300:])

    def _fetch(self, entry):
        self.progress(f"unduh {entry['relpath']}")
        dest = os.path.join(self.work, f"dl-{entry['id']}")
        self.client.download(self.job["id"], entry, dest)
        return dest

    # ---------- jenis uji ----------
    def test_full(self, entry):
        source = self._fetch(entry)
        self.progress("ekstrak dan prepare full")
        data = os.path.join(self.work, "data")
        os.makedirs(data, exist_ok=True)
        stream = os.path.join(self.work, "full.xbstream")
        self._decrypt(source, stream)
        code, _, err = self._tool("set -e; mbstream -x -C /work/data < /work/full.xbstream && "
                                  "mariadb-backup --prepare --target-dir=/work/data > /work/prepare.log 2>&1 "
                                  "|| { tail -5 /work/prepare.log; exit 2; }; chown -R mysql:mysql /work/data")
        if code != 0:
            raise Failed("prepare", (err or "mariadb-backup --prepare gagal").strip()[-LOG_LIMIT:])
        if os.path.exists(stream):
            os.unlink(stream)
        self.progress("jalankan MariaDB hasil restore")
        name = self._start("full", ["--entrypoint", "mariadbd", "-v", f"{data}:/var/lib/mysql", self.config["image"],
                                   "--user=mysql", f"--socket={SOCKET}", "--skip-grant-tables", "--skip-networking",
                                   "--event-scheduler=OFF", "--log-bin=OFF"])
        self._wait_ready(name)
        schemas = ",".join(f"'{s}'" for s in SYSTEM_SCHEMAS)
        code, out, err = self._sql(name, "SELECT table_schema, table_name FROM information_schema.tables WHERE "
                                         f"table_type='BASE TABLE' AND table_schema NOT IN ({schemas}) "
                                         "ORDER BY data_length DESC")
        if code != 0:
            raise Failed("daftar tabel", err.strip()[-300:])
        per_db = {}
        for line in out.splitlines():
            schema, _, table = line.partition("\t")
            per_db.setdefault(schema, []).append(table)
        for schema, tables in per_db.items():
            for table in tables[:MAIN_TABLES]:
                self.progress(f"baca {schema}.{table}")
                code, _, err = self._sql(name, f"SELECT COUNT(*) FROM `{schema}`.`{table}`")
                if code != 0:
                    raise Failed(f"baca {schema}.{table}", err.strip()[-300:])
        self.table_counts = {schema: len(tables) for schema, tables in per_db.items()}
        self.full_ok = True
        self._remove(name)
        purge_dir(data, self.config, self.run)

    def _import_dump(self, entry, label):
        source = self._fetch(entry)
        sql = os.path.join(self.work, f"{label}.sql")
        self._decrypt(source, sql)
        password = secrets.token_hex(16)
        self.progress(f"impor {label}")
        name = self._start(f"dump-{entry['id']}", ["-e", f"MARIADB_ROOT_PASSWORD={password}", self.config["image"]])
        self._wait_ready(name, password, init=True)
        code, _, err = self._sql(name, "", password, stdin_path=sql)
        if code != 0:
            raise Failed(f"impor {label}", err.strip()[-LOG_LIMIT:])
        return name, password

    def test_dump(self, entry):
        database = entry["database"]
        name, password = self._import_dump(entry, f"dump-{database}")
        code, out, err = self._sql(name, f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{database}' "
                                         "AND table_type='BASE TABLE'", password)
        if code != 0 or not out.strip().isdigit():
            raise Failed("hitung tabel", err.strip()[-300:])
        count = int(out.strip())
        expected = self.table_counts.get(database)
        if self.full_ok and expected is not None and count != expected:
            raise Failed("jumlah tabel", f"dump {database} punya {count} tabel, hasil restore full {expected}")
        code, out, _ = self._sql(name, "SELECT table_name FROM information_schema.tables WHERE "
                                       f"table_schema='{database}' AND table_type='BASE TABLE' ORDER BY data_length DESC "
                                       f"LIMIT {MAIN_TABLES}", password)
        for table in out.split():
            code, _, err = self._sql(name, f"SELECT COUNT(*) FROM `{database}`.`{table}`", password)
            if code != 0:
                raise Failed(f"baca {database}.{table}", err.strip()[-300:])
        self._remove(name)

    def test_users(self, entry):
        name, password = self._import_dump(entry, "users")
        code, out, err = self._sql(name, "SELECT COUNT(*) FROM mysql.global_priv", password)
        if code != 0 or not out.strip().isdigit() or int(out.strip()) < 1:
            raise Failed("baca akun", err.strip()[-300:] or "tidak ada akun")
        self._remove(name)

    def test_binlog(self, entries, start):
        """Metadata saja: nomor berurutan tanpa lompatan, mulai dari binlog_start, dan tiap file lolos checksum."""
        ordered = sorted(entries, key=lambda e: e["binlog_no"])
        numbers = [e["binlog_no"] for e in ordered]
        if start is not None and numbers and numbers[0] > start:
            raise Failed("awal rantai", f"binlog {start} tidak ada; rantai mulai dari {numbers[0]}")
        for before, after in zip(numbers, numbers[1:], strict=False):
            if after != before + 1:
                raise Failed("rantai putus", f"nomor {before + 1} sampai {after - 1} hilang")
        for entry in ordered:
            self._fetch(entry)

    # ---------- tugas ----------
    def _remove(self, name):
        self.run(["docker", "rm", "-f", "-v", name])
        if name in self.containers:
            self.containers.remove(name)

    def execute(self):
        """Daftar hasil per jenis dan status keseluruhan."""
        files = self.job["files"]
        os.makedirs(self.work, exist_ok=True)
        results = []

        def attempt(kind, entry, action, database=None):
            started = time.time()
            item = {"kind": kind, "database": database, "backup_id": entry["id"] if entry else None}
            try:
                action()
                item["status"] = "Lolos"
            except Failed as exc:
                item.update(status="Gagal", failed_step=exc.step[:200], log=exc.message[:LOG_LIMIT])
            except NotRun as exc:
                item.update(status="Tidak terlaksana", failed_step=exc.step[:200], log=exc.message[:LOG_LIMIT])
            item["duration_seconds"] = int(time.time() - started)
            results.append(item)

        try:
            free = shutil.disk_usage(self.config["scratch_dir"]).free
            need = sum(f["size"] for f in files if f["kind"] == "fullbackup") * 6
            if free < need:
                raise NotRun("scratch", f"ruang scratch {free} byte, perlu sekitar {need}")
            fulls = [f for f in files if f["kind"] == "fullbackup"]
            for entry in fulls:
                attempt("full", entry, lambda e=entry: self.test_full(e))
                self._remove_all()
            for entry in (f for f in files if f["kind"] == "databases"):
                if entry["database"] == "_users":
                    attempt("users", entry, lambda e=entry: self.test_users(e))
                else:
                    attempt("dump", entry, lambda e=entry: self.test_dump(e), entry["database"])
                self._remove_all()
            binlogs = [f for f in files if f["kind"] == "binlog"]
            if binlogs:
                start = _binlog_number(fulls[0].get("binlog_start")) if fulls else None
                attempt("binlog", binlogs[-1], lambda: self.test_binlog(binlogs, start))
        except NotRun as exc:
            results.append({"kind": "full", "status": "Tidak terlaksana", "failed_step": exc.step[:200],
                            "log": exc.message[:LOG_LIMIT], "backup_id": None, "database": None})
        finally:
            self._remove_all()
            purge_dir(self.work, self.config, self.run)
        statuses = {r["status"] for r in results}
        overall = "Gagal" if "Gagal" in statuses else "Tidak terlaksana" if "Tidak terlaksana" in statuses or not results else "Lolos"
        return overall, results

    def _remove_all(self):
        for name in list(self.containers):
            self._remove(name)


STANDBY_LABEL = "lumbung.standby=1"
PROMOTED_LABEL = "lumbung.promoted=1"
DEFAULT_STANDBY_PORT = 33100
BINLOG_NAME = re.compile(r"mysql-bin\.(\d+)")


def standby_container(container):
    return f"lumbung-standby-{container}"


class StandbyRunner(Restorer):
    """Memulihkan full + binlog menjadi database cadangan yang tetap hidup, atau menerapkan binlog baru ke database
    cadangan yang ada. Folder per container: current/ (datadir hidup), new/ (sedang disiapkan), password akun runner
    di current.pw dan new.pw (izin 600, tidak pernah dikirim ke LumbungDB)."""

    def __init__(self, config, client, job, runner=run, progress=None):
        super().__init__(config, client, job, runner, progress)
        self.base = os.path.join(config["scratch_dir"], "standby", job["container"])
        self.work = self.base           # alat dijalankan dengan folder ini sebagai /work
        self.name = standby_container(job["container"])
        self.extra = {}

    # ---------- bantuan ----------
    def _paths(self, which):
        return os.path.join(self.base, which), os.path.join(self.base, f"{which}.pw")

    def _password(self, which):
        with open(self._paths(which)[1]) as handle:
            return handle.read().strip()

    def _port(self):
        known = (self.job.get("standby") or {}).get("port")
        if known:
            return known
        used = set()
        parent = os.path.dirname(self.base)
        for entry in os.listdir(parent) if os.path.isdir(parent) else []:
            try:
                with open(os.path.join(parent, entry, "state.json")) as handle:
                    used.add(json.load(handle).get("port"))
            except (OSError, ValueError):
                continue
        port = self.config.get("standby_port_base", DEFAULT_STANDBY_PORT)
        while port in used:
            port += 1
        return port

    def _write_state(self, port):
        with open(os.path.join(self.base, "state.json"), "w") as handle:
            json.dump({"port": port}, handle)

    def _exec(self, container, password, sql=None, stdin_path=None):
        args = ["docker", "exec", *(["-i"] if stdin_path else []), "-e", f"MYSQL_PWD={password}", container, "mariadb",
                "-S", SOCKET, "-ulumbung_runner", "-BN"]
        if sql is not None:
            args += ["-e", sql]
        return self.run(args, 7200, stdin_path)

    def _wait_account(self, container, password):
        for _ in range(self.config["ready_seconds"]):
            code, out, _ = self._exec(container, password, "SELECT 1")
            if code == 0 and out.strip() == "1":
                return
            time.sleep(1)
        _, logs, err = self.run(["docker", "logs", "--tail", "15", container])
        raise Failed("MariaDB tidak naik", (logs + err).strip()[-LOG_LIMIT:])

    def _mariadbd(self, name, datadir, label, extra_args, extra_docker=()):
        full = name
        code, _, err = self.run(["docker", "run", "-d", "--name", full, "--label", label, *extra_docker,
                                 "--entrypoint", "mariadbd", "-v", f"{datadir}:/var/lib/mysql", self.config["image"],
                                 "--user=mysql", f"--socket={SOCKET}", "--event-scheduler=OFF", "--log-bin=OFF",
                                 *extra_args])
        if code != 0:
            raise NotRun(f"jalankan {name}", err.strip()[-300:])
        return full

    def _stop(self, name):
        self.run(["docker", "stop", "-t", "120", name], 180)
        self.run(["docker", "rm", "-f", "-v", name])

    def _binlog_entries(self):
        return sorted((f for f in self.job["files"] if f["kind"] == "binlog"), key=lambda f: f["binlog_no"])

    def _apply_binlogs(self, container, password, entries, start_position=None, first_name=None):
        """Binlog dibuka dan diubah jadi SQL di alat, lalu dimasukkan lewat akun runner. Penerapan ditandai dulu: bila
        runner mati di tengah, penerapan berikutnya menolak, karena sebagian perubahan sudah masuk."""
        if not entries:
            return None
        names = []
        for entry in entries:
            source = self._fetch(entry)
            number = f"{entry['binlog_no']:06d}"
            decoded = f"binlog/mysql-bin.{number}"
            os.makedirs(os.path.join(self.base, "binlog"), exist_ok=True)
            self._decrypt(source, os.path.join(self.base, decoded))
            names.append(f"/work/{decoded}")
        position = f"--start-position={start_position} " if start_position else ""
        self.progress("ubah binlog menjadi SQL")
        code, _, err = self._tool(f"set -eo pipefail; mariadb-binlog {position}{' '.join(names)} > /work/apply.sql")
        if code != 0:
            raise Failed("baca binlog", err.strip()[-LOG_LIMIT:])
        marker = os.path.join(self.base, "applying")
        with open(marker, "w") as handle:
            handle.write(str(self.job["id"]))
        self.progress("terapkan binlog")
        code, _, err = self._exec(container, password, stdin_path=os.path.join(self.base, "apply.sql"))
        if code != 0:
            raise Failed("terapkan binlog", err.strip()[-LOG_LIMIT:])
        os.unlink(marker)
        return entries[-1]["binlog_no"]

    # ---------- pemulihan full ----------
    def restore_full(self):
        fulls = [f for f in self.job["files"] if f["kind"] == "fullbackup"]
        if len(fulls) != 1:
            raise NotRun("tugas", "tugas harus berisi tepat satu full")
        full = fulls[0]
        new_dir, new_pw = self._paths("new")
        cur_dir, cur_pw = self._paths("current")
        purge_dir(new_dir, self.config, self.run)
        os.makedirs(new_dir, exist_ok=True)
        source = self._fetch(full)
        self.progress("ekstrak dan prepare full")
        stream = os.path.join(self.base, "full.xbstream")
        self._decrypt(source, stream)
        code, _, err = self._tool("set -e; mbstream -x -C /work/new < /work/full.xbstream && "
                                  "mariadb-backup --prepare --target-dir=/work/new > /work/prepare.log 2>&1 "
                                  "|| { tail -5 /work/prepare.log; exit 2; }; chown -R mysql:mysql /work/new")
        if code != 0:
            raise Failed("prepare", (err or "mariadb-backup --prepare gagal").strip()[-LOG_LIMIT:])
        os.unlink(stream)
        # Dibaca lewat alat: isi folder dimiliki mysql dan tidak terbaca oleh user runner di host.
        code, out, _ = self._tool("cat /work/new/mariadb_backup_binlog_info")
        parts = out.split() if code == 0 else []
        position = parts[1] if len(parts) >= 2 and parts[1].isdigit() else None
        if self._binlog_entries() and position is None:
            raise Failed("posisi binlog", "mariadb_backup_binlog_info tidak terbaca; binlog tidak diterapkan dari awal file")
        self.progress(f"posisi awal binlog {position}")
        password = secrets.token_hex(16)
        self.progress("buat akun runner")
        temp = self._mariadbd(f"{self.name}-akun", new_dir, LABEL, ["--skip-grant-tables", "--skip-networking"],
                              ["--network", "none"])
        self.containers.append(temp)
        self._wait_ready(temp)
        sql = ("FLUSH PRIVILEGES; "
               f"CREATE USER 'lumbung_runner'@'localhost' IDENTIFIED BY '{password}'; "
               "GRANT ALL PRIVILEGES ON *.* TO 'lumbung_runner'@'localhost' WITH GRANT OPTION;")
        code, _, err = self._sql(temp, sql)
        if code != 0:
            raise Failed("buat akun runner", err.strip()[-300:])
        self._stop(temp)
        self.containers.remove(temp)
        fd = os.open(new_pw, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(password)

        check = self._mariadbd(f"{self.name}-uji", new_dir, LABEL, ["--skip-networking", "--read-only"],
                               ["--network", "none"])
        self.containers.append(check)
        self._wait_account(check, password)
        applied = self._apply_binlogs(check, password, self._binlog_entries(), position)
        self._verify_tables(check, password)
        self._stop(check)
        self.containers.remove(check)
        with SHUTDOWN.guard():
            port = self._swap(new_dir, new_pw, cur_dir, cur_pw, password)
        self.extra = {"standby": {"full_backup_id": full["id"], "applied_binlog_no": applied
                                  if applied is not None else _binlog_number(full.get("binlog_start")) - 1
                                  if _binlog_number(full.get("binlog_start")) is not None else None, "port": port}}

    def _verify_tables(self, container, password):
        schemas = ",".join(f"'{s}'" for s in SYSTEM_SCHEMAS)
        code, out, err = self._exec(container, password, "SELECT table_schema, table_name FROM information_schema.tables "
                                    f"WHERE table_type='BASE TABLE' AND table_schema NOT IN ({schemas}) "
                                    "ORDER BY data_length DESC")
        if code != 0:
            raise Failed("daftar tabel", err.strip()[-300:])
        per_db = {}
        for line in out.splitlines():
            schema, _, table = line.partition("\t")
            per_db.setdefault(schema, []).append(table)
        for schema, tables in per_db.items():
            for table in tables[:MAIN_TABLES]:
                code, _, err = self._exec(container, password, f"SELECT COUNT(*) FROM `{schema}`.`{table}`")
                if code != 0:
                    raise Failed(f"baca {schema}.{table}", err.strip()[-300:])

    def _swap(self, new_dir, new_pw, cur_dir, cur_pw, password):
        """Database cadangan lama baru diganti setelah yang baru terbukti naik; bila gagal, yang lama dihidupkan lagi."""
        port = self._port()
        old_dir, old_pw = os.path.join(self.base, "old"), os.path.join(self.base, "old.pw")
        had_old = os.path.isdir(cur_dir)
        purge_dir(old_dir, self.config, self.run)
        if had_old:
            self._stop(self.name)
            os.rename(cur_dir, old_dir)
            if os.path.exists(cur_pw):
                os.rename(cur_pw, old_pw)
        os.rename(new_dir, cur_dir)
        os.rename(new_pw, cur_pw)
        bind = self.config.get("standby_bind", "127.0.0.1")
        try:
            self._mariadbd(self.name, cur_dir, STANDBY_LABEL, ["--read-only", "--bind-address=0.0.0.0"],
                           ["--restart", "unless-stopped", "-p", f"{bind}:{port}:3306"])
            self._wait_account(self.name, password)
        except NotRun:
            self._stop(self.name)
            purge_dir(cur_dir, self.config, self.run)
            if os.path.exists(cur_pw):
                os.unlink(cur_pw)
            if had_old:
                os.rename(old_dir, cur_dir)
                os.rename(old_pw, cur_pw)
                self.run(["docker", "run", "-d", "--name", self.name, "--label", STANDBY_LABEL, "--restart",
                          "unless-stopped", "-p", f"{bind}:{port}:3306", "--entrypoint", "mariadbd", "-v",
                          f"{cur_dir}:/var/lib/mysql", self.config["image"], "--user=mysql", f"--socket={SOCKET}",
                          "--event-scheduler=OFF", "--log-bin=OFF", "--read-only", "--bind-address=0.0.0.0"])
            raise
        self._write_state(port)
        purge_dir(old_dir, self.config, self.run)
        if os.path.exists(old_pw):
            os.unlink(old_pw)
        return port

    # ---------- naikkan ke production ----------
    def _offline_sql(self, datadir, suffix, sql):
        """SQL pada datadir lewat server sementara tanpa jaringan dan tanpa tabel hak akses, seperti saat membuat akun
        runner. Server dimatikan lagi sesudahnya."""
        temp = self._mariadbd(f"{self.name}-{suffix}", datadir, LABEL, ["--skip-grant-tables", "--skip-networking"],
                              ["--network", "none"])
        self.containers.append(temp)
        try:
            self._wait_ready(temp)
            code, _, err = self._sql(temp, "FLUSH PRIVILEGES; " + sql)
            if code != 0:
                raise Failed(f"ubah akun ({suffix})", err.strip()[-300:])
        finally:
            self._stop(temp)
            if temp in self.containers:
                self.containers.remove(temp)

    def _move_dir(self, src, dst):
        """Pindah datadir antar-induk. Isi dan folder datadir dimiliki mysql, jadi rename dari user runner di host bisa
        ditolak (memindahkan folder ke induk lain butuh izin tulis pada foldernya sendiri). Bila ditolak, dipindah dari
        dalam container dengan satu mount yang menaungi keduanya, jadi tetap rename dan bukan salin."""
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            pass
        common = os.path.commonpath([src, dst])
        code, _, err = self.run(["docker", "run", "--rm", "--label", LABEL, "--network", "none", "--entrypoint", "bash",
                                 "-v", f"{common}:/m", self.config["image"], "-c",
                                 f"mv /m/{os.path.relpath(src, common)} /m/{os.path.relpath(dst, common)}"], 600)
        if code != 0:
            raise NotRun("pindah datadir", err.strip()[-300:])

    def _start_standby(self, cur_dir, port):
        bind = self.config.get("standby_bind", "127.0.0.1")
        self.run(["docker", "run", "-d", "--name", self.name, "--label", STANDBY_LABEL, "--restart", "unless-stopped",
                  "-p", f"{bind}:{port}:3306", "--entrypoint", "mariadbd", "-v", f"{cur_dir}:/var/lib/mysql",
                  self.config["image"], "--user=mysql", f"--socket={SOCKET}", "--event-scheduler=OFF", "--log-bin=OFF",
                  "--read-only", "--bind-address=0.0.0.0"])

    def promote(self):
        """Database cadangan menjadi container production bernama baru di mesin ini.

        Urutan: user backup baru dibuat dan akun runner dihapus pada datadir (server sementara), datadir dipindah ke
        folder container baru, lalu container dijalankan lewat entrypoint image dengan .env berizin 600. Bila ada yang
        gagal sebelum container terbukti naik dengan user backup baru, datadir dikembalikan, akun runner dibuat lagi,
        dan database cadangan dihidupkan seperti semula."""
        info = (self.job.get("standby") or {}).get("promote")
        if not info:
            raise NotRun("tugas", "data penaikan tidak terbaca (nama atau token agen tidak ada)")
        new_name, port, token = info["name"], int(info["port"]), info["token"]
        cur_dir, cur_pw = self._paths("current")
        if not os.path.isdir(cur_dir):
            raise NotRun("database cadangan", "belum ada database cadangan di runner ini")
        if os.path.exists(os.path.join(self.base, "applying")):
            raise Failed("terputus saat menerapkan", "penerapan binlog terputus; jalankan Restore sekarang dulu")
        code, out, _ = self.run(["docker", "ps", "-a", "--filter", f"name=^{new_name}$", "-q"])
        if code == 0 and out.strip():
            raise NotRun("nama container", f"container bernama {new_name} sudah ada di mesin ini")
        target = os.path.join(self.config["promote_dir"], new_name)
        if os.path.exists(target):
            raise NotRun("folder", f"{target} sudah ada")
        runner_password = self._password("current")
        backup_password = secrets.token_hex(16)
        state_port = (self.job.get("standby") or {}).get("port") or self._port()

        self.progress("hentikan database cadangan")
        self._stop(self.name)
        self.progress("buat user backup baru dan hapus akun runner")
        try:
            self._offline_sql(cur_dir, "naik", (
                "CREATE OR REPLACE USER 'user_backup'@'localhost' "
                f"IDENTIFIED BY '{backup_password}'; "
                "GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER, RELOAD, PROCESS, BINLOG MONITOR ON *.* "
                "TO 'user_backup'@'localhost'; "
                "CREATE USER IF NOT EXISTS 'lumbung'@'localhost' IDENTIFIED VIA unix_socket; "
                "GRANT SHOW VIEW ON *.* TO 'lumbung'@'localhost'; "
                "DROP USER IF EXISTS 'lumbung_runner'@'localhost'; FLUSH PRIVILEGES;"))
        except (NotRun, Failed):
            self._start_standby(cur_dir, state_port)
            raise
        data = os.path.join(target, "data")
        moved = False
        try:
            os.makedirs(target, mode=0o700)
            self._move_dir(cur_dir, data)
            moved = True
            env_path = os.path.join(target, ".env")
            fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write("\n".join((
                    f"LUMBUNG_URL={self.client.base_url}", f"LUMBUNG_TOKEN={token}",
                    f"LUMBUNG_AGE_RECIPIENTS={info['age_recipients']}",
                    "BACKUP_USER=user_backup", f"BACKUP_PASS={backup_password}",
                    "BACKUP_CRON=", "DUMP_CRON=", "BINLOG_CRON=", "BINLOG_UNSENT_MAX_DAYS=3",
                    "MARIADB_AUTO_UPGRADE=1", "")))
            self.progress("jalankan container production")
            code, _, err = self.run([
                "docker", "run", "-d", "--name", new_name, "--hostname", new_name, "--restart", "always",
                "--label", PROMOTED_LABEL, "--env-file", env_path, "-p", f"{port}:3306",
                "--memory", self.config["promote_memory"], "--cpus", self.config["promote_cpus"],
                "-v", f"{data}:/var/lib/mysql", "-v", f"{new_name}-backup:/backup",
                "-v", "/etc/hostname:/lumbung/server/hostname:ro", "-v", "/etc/machine-id:/lumbung/server/machine-id:ro",
                self.config["image"], "--event-scheduler=ON"])
            if code != 0:
                raise NotRun("jalankan container production", err.strip()[-300:])
            self._wait_production(new_name, backup_password)
        except (NotRun, Failed, OSError):
            self.progress("batalkan: kembalikan database cadangan")
            self.run(["docker", "rm", "-f", "-v", new_name])
            if moved:
                self._move_dir(data, cur_dir)
            purge_dir(target, self.config, self.run)
            try:
                self._offline_sql(cur_dir, "balik", (
                    f"CREATE OR REPLACE USER 'lumbung_runner'@'localhost' IDENTIFIED BY '{runner_password}'; "
                    "GRANT ALL PRIVILEGES ON *.* TO 'lumbung_runner'@'localhost' WITH GRANT OPTION; FLUSH PRIVILEGES;"))
            finally:
                self._start_standby(cur_dir, state_port)
                try:
                    self._wait_account(self.name, runner_password)
                except (NotRun, Failed):
                    pass         # hasilnya tetap penaikan gagal; database cadangan diperiksa di restore berikutnya
            raise
        for leftover in (cur_pw, os.path.join(self.base, "state.json")):
            if os.path.exists(leftover):
                os.unlink(leftover)
        self.note_ok = f"{new_name} berjalan di port {port}"

    def _wait_production(self, name, password):
        for _ in range(self.config["ready_seconds"]):
            # Server sementara milik entrypoint (upgrade) juga menjawab di socket, tapi tanpa jaringan.
            code, out, _ = self.run(["docker", "exec", "-e", f"MYSQL_PWD={password}", name, "mariadb", "-S", SOCKET,
                                     "-uuser_backup", "-BN", "-e",
                                     "SELECT @@global.skip_networking, @@global.read_only, @@global.event_scheduler, "
                                     "(SELECT COUNT(*) FROM mysql.global_priv WHERE user='lumbung_runner')"], 60)
            if code == 0 and out.strip():
                fields = out.split()
                if fields[0] != "0":
                    time.sleep(1)
                    continue
                if fields[1] != "0":
                    raise Failed("read_only", "container production masih read_only")
                if fields[2] != "ON":
                    raise Failed("event scheduler", f"event scheduler {fields[2]}, seharusnya ON")
                if fields[3] != "0":
                    raise Failed("akun runner", "akun lumbung_runner masih ada di hasil restore")
                return
            time.sleep(1)
        _, logs, err = self.run(["docker", "logs", "--tail", "15", name])
        raise Failed("MariaDB production tidak naik", (logs + err).strip()[-LOG_LIMIT:])

    # ---------- penerapan binlog ----------
    def apply_binlogs(self):
        cur_dir, _ = self._paths("current")
        if not os.path.isdir(cur_dir):
            raise NotRun("database cadangan", "belum ada database cadangan di runner ini")
        if os.path.exists(os.path.join(self.base, "applying")):
            raise Failed("terputus saat menerapkan", "penerapan sebelumnya terputus; tunggu pemulihan full berikutnya")
        code, out, _ = self.run(["docker", "inspect", "-f", "{{.State.Running}}", self.name])
        if code != 0 or out.strip() != "true":
            raise NotRun("database cadangan", "container database cadangan tidak berjalan")
        password = self._password("current")
        applied = self._apply_binlogs(self.name, password, self._binlog_entries())
        self.extra = {"standby": {"applied_binlog_no": applied}}

    def remove(self):
        """Menghapus database cadangan di mesin ini: container, datadir, password, dan catatan port."""
        cur_dir, cur_pw = self._paths("current")
        self.progress("hentikan dan hapus database cadangan")
        self._stop(self.name)
        purge_dir(cur_dir, self.config, self.run)
        for name in (cur_pw, os.path.join(self.base, "state.json")):
            if os.path.exists(name):
                os.unlink(name)

    def execute(self):
        os.makedirs(self.base, exist_ok=True)
        started = time.time()
        item = {"kind": "full", "backup_id": None, "database": None}
        try:
            free = shutil.disk_usage(self.config["scratch_dir"]).free
            need = sum(f["size"] for f in self.job["files"] if f["kind"] == "fullbackup") * 6
            if free < need:
                raise NotRun("scratch", f"ruang scratch {free} byte, perlu sekitar {need}")
            if self.job["kind"] == "standby_full":
                self.restore_full()
            elif self.job["kind"] == "standby_remove":
                self.remove()
            elif self.job["kind"] == "standby_promote":
                with SHUTDOWN.guard():
                    self.promote()
            else:
                self.apply_binlogs()
            status, note = "Lolos", getattr(self, "note_ok", None)
        except Failed as exc:
            status, note = "Gagal", f"{exc.step}: {exc.message}"[:500]
        except NotRun as exc:
            status, note = "Tidak terlaksana", f"{exc.step}: {exc.message}"[:500]
        finally:
            self._remove_all()
            for leftover in ("binlog", "new", "apply.sql", "full.xbstream", "prepare.log"):
                path = os.path.join(self.base, leftover)
                if os.path.isdir(path):
                    purge_dir(path, self.config, self.run)
                elif os.path.exists(path):
                    os.unlink(path)
            for entry in glob.glob(os.path.join(self.base, "dl-*")):
                os.unlink(entry)
        item["duration_seconds"] = int(time.time() - started)
        self.note = note
        return status, []


MANAGED_LABEL = "lumbung.managed=1"
CONTROL_KINDS = ("container_install", "container_start", "container_stop", "container_restart", "container_update",
                 "container_adopt")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/@+=,-]*$")


def random_text(length=32):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def random_user(prefix):
    return f"{prefix}_" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(12))


class ContainerWorker:
    """Tugas jalur kontrol: memasang container MariaDB baru di mesin ini, dan start/stop/restart container yang dipasang
    atau dinaikkan lewat runner. Container lain di mesin ini tidak pernah disentuh: labelnya diperiksa dulu."""

    def __init__(self, config, client, job, runner=run, progress=None):
        self.config, self.client, self.job, self.run = config, client, job, runner
        self.progress = progress or (lambda step: None)
        self.note = None
        self.managed = None          # {port, image_tag} untuk dicatat LumbungDB sesudah ambil alih

    def _labels(self, name):
        code, out, _ = self.run(["docker", "inspect", "-f", "{{json .Config.Labels}}", name], 60)
        if code != 0:
            return None
        try:
            return json.loads(out.strip() or "{}") or {}
        except ValueError:
            return {}

    def _state(self, name):
        code, out, _ = self.run(["docker", "inspect", "-f", "{{.State.Status}}", name], 60)
        return out.strip() if code == 0 else None

    # ---------- start / stop / restart ----------
    def power(self, action):
        name = self.job["container"]
        labels = self._labels(name)
        if labels is None:
            raise NotRun("container", f"container {name} tidak ada di mesin ini")
        if labels.get("lumbung.managed") != "1" and labels.get("lumbung.promoted") != "1":
            raise NotRun("container", f"{name} tidak dipasang lewat runner; tidak disentuh")
        self.progress(f"{action} {name}")
        args = {"start": ["docker", "start", name], "stop": ["docker", "stop", "-t", "120", name],
                "restart": ["docker", "restart", "-t", "120", name]}[action]
        code, _, err = self.run(args, 300)
        if code != 0:
            raise NotRun(action, err.strip()[-300:])
        self.note = f"{name}: {self._state(name) or 'tidak terbaca'}"

    # ---------- update image ----------
    def _env_file(self, name, labels):
        """.env container ini: dari pemasangan lewat runner atau dari Naikkan ke production."""
        base = self.config["container_dir"] if labels.get("lumbung.managed") == "1" else self.config["promote_dir"]
        path = os.path.join(base, name, ".env")
        if not os.path.isfile(path):
            raise NotRun(".env", f"{path} tidak ada; container tidak bisa dibuat ulang dengan password yang sama")
        return path

    def _recreate_args(self, name, inspected, env_path, image, extra_labels=()):
        """Argumen docker run dari container lama: nama, hostname, restart, label lumbung, port, batas sumber daya,
        volume/bind, healthcheck, dan argumen MariaDB. ENV tidak disalin dari inspect (berisi ENV image lama, mis.
        MARIADB_VERSION); semua nilai sendiri ada di .env."""
        config, host = inspected.get("Config") or {}, inspected.get("HostConfig") or {}
        args = ["docker", "run", "-d", "--name", name, "--hostname", config.get("Hostname") or name,
                "--restart", (host.get("RestartPolicy") or {}).get("Name") or "always", "--env-file", env_path]
        labels = {k: v for k, v in (config.get("Labels") or {}).items() if k.startswith("lumbung.")}
        for extra in extra_labels:
            key, _, value = extra.partition("=")
            labels[key] = value
        for key, value in sorted(labels.items()):
            args += ["--label", f"{key}={value}"]
        for container_port, bindings in sorted((host.get("PortBindings") or {}).items()):
            for binding in bindings or []:
                address = f"{binding['HostIp']}:" if binding.get("HostIp") else ""
                args += ["-p", f"{address}{binding.get('HostPort')}:{container_port.split('/')[0]}"]
        if host.get("Memory"):
            args += ["--memory", f"{host['Memory']}b"]
        if host.get("NanoCpus"):
            args += ["--cpus", f"{host['NanoCpus'] / 1e9:g}"]
        for mount in inspected.get("Mounts") or []:
            source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
            if mount.get("Type") not in ("volume", "bind") or not source:
                continue
            args += ["-v", f"{source}:{mount['Destination']}{'' if mount.get('RW', True) else ':ro'}"]
        health = config.get("Healthcheck") or {}
        test = health.get("Test") or []
        if len(test) == 2 and test[0] == "CMD-SHELL":
            args += ["--health-cmd", test[1]]
            for flag, key in (("--health-interval", "Interval"), ("--health-timeout", "Timeout"),
                              ("--health-start-period", "StartPeriod")):
                if health.get(key):
                    args += [flag, f"{health[key] // 1_000_000_000}s"]
            if health.get("Retries"):
                args += ["--health-retries", str(health["Retries"])]
        args.append(image)
        args += [str(a) for a in config.get("Cmd") or []]
        return args

    def _wait_up(self, name):
        """Sehat menurut healthcheck container; tanpa healthcheck, healthcheck.sh image dipanggil langsung."""
        for _ in range(max(self.config["ready_seconds"], 600) // 5):
            code, out, _ = self.run(["docker", "inspect", "-f",
                                     "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", name], 60)
            parts = out.split() if code == 0 else []
            if parts and parts[0] in ("exited", "dead"):
                break
            if len(parts) > 1:
                if parts[1] == "healthy":
                    return
                if parts[1] == "unhealthy":
                    break
            elif parts and parts[0] == "running":
                code, _, _ = self.run(["docker", "exec", name, "healthcheck.sh", "--connect", "--innodb_initialized"], 60)
                if code == 0:
                    return
            time.sleep(5)
        _, logs, err = self.run(["docker", "logs", "--tail", "15", name])
        raise Failed("MariaDB tidak sehat dengan image baru", (logs + err).strip()[-LOG_LIMIT:])

    def update_image(self):
        name = self.job["container"]
        image = (self.job.get("update") or {}).get("image", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_./-]*:[A-Za-z0-9_.-]{1,64}", image):
            raise NotRun("tugas", "image tujuan tidak terbaca")
        labels = self._labels(name)
        if labels is None:
            raise NotRun("container", f"container {name} tidak ada di mesin ini")
        if labels.get("lumbung.managed") != "1" and labels.get("lumbung.promoted") != "1":
            raise NotRun("container", f"{name} tidak dipasang lewat runner; tidak disentuh")
        env_path = self._env_file(name, labels)
        inspected = self._inspect(name)
        old_image = (inspected.get("Config") or {}).get("Image", "")
        args = self._recreate_args(name, inspected, env_path, image)
        self.progress(f"unduh image {image}")
        code, _, err = self.run(["docker", "pull", image], 3600)
        if code != 0:
            raise NotRun("unduh image", err.strip()[-300:])
        self._replace(name, inspected, args, "jalankan dengan image baru", "tunggu MariaDB sehat (termasuk mariadb-upgrade)")
        self.note = f"{name}: {old_image} → {image}"

    def _inspect(self, name):
        code, out, err = self.run(["docker", "inspect", name], 60)
        try:
            inspected = json.loads(out)[0] if code == 0 else None
        except (ValueError, IndexError):
            inspected = None
        if not inspected:
            raise NotRun("inspect", err.strip()[-300:] or "inspect tidak terbaca")
        return inspected

    def _replace(self, name, inspected, args, run_step, wait_step):
        """Ganti container dengan yang dibuat dari `args`, data tetap di volume yang sama. Yang lama dihentikan dan
        diganti namanya dulu; bila yang baru gagal naik atau tidak sehat, yang lama dijalankan lagi."""
        old_restart = ((inspected.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "always"
        old = f"{name}-lama-{self.job['id']}"
        self.progress("hentikan container lama")
        # Restart dimatikan dulu: container lama tidak boleh hidup lagi bersama yang baru di volume yang sama.
        self.run(["docker", "update", "--restart", "no", name], 60)
        code, _, err = self.run(["docker", "stop", "-t", "120", name], 300)
        if code != 0:
            self.run(["docker", "update", "--restart", old_restart, name], 60)
            raise NotRun("hentikan container", err.strip()[-300:])
        code, _, err = self.run(["docker", "rename", name, old], 60)
        if code != 0:
            self.run(["docker", "update", "--restart", old_restart, name], 60)
            self.run(["docker", "start", name], 300)
            raise NotRun("ganti nama container lama", err.strip()[-300:])
        try:
            self.progress(run_step)
            code, _, err = self.run(args, 600)
            if code != 0:
                raise NotRun("jalankan container baru", err.strip()[-300:])
            self.progress(wait_step)
            self._wait_up(name)
        except NotRun:
            self.progress("batalkan: kembalikan container lama")
            self.run(["docker", "rm", "-f", name], 120)          # tanpa -v: volume data tetap
            self.run(["docker", "rename", old, name], 60)
            self.run(["docker", "update", "--restart", old_restart, name], 60)
            self.run(["docker", "start", name], 300)
            raise
        self.run(["docker", "rm", old], 120)                       # tanpa -v: volume dipakai container baru

    # ---------- kelola lewat runner (ambil alih) ----------
    @staticmethod
    def _data_port(inspected):
        for binding in ((inspected.get("HostConfig") or {}).get("PortBindings") or {}).get("3306/tcp") or []:
            if str(binding.get("HostPort", "")).isdigit():
                return int(binding["HostPort"])
        return None

    def _tag_of(self, image):
        return image.split(":lumbungdb-", 1)[1] if ":lumbungdb-" in image else None

    def adopt(self):
        """Container MariaDB yang dipasang di luar runner (mis. docker compose dari script pasang) dibuat ulang dengan
        label lumbung.managed=1, data, nama, port, dan image yang sama. .env disusun dari environment container
        dikurangi ENV bawaan image, jadi folder compose lama tidak perlu dibaca. Label compose tidak disalin: compose
        tidak lagi menganggap container ini miliknya."""
        name = self.job["container"]
        labels = self._labels(name)
        if labels is None:
            raise NotRun("container", f"container bernama {name} tidak ada di mesin runner ini")
        inspected = self._inspect(name)
        config = inspected.get("Config") or {}
        image = config.get("Image", "")
        port = self._data_port(inspected)
        if port is None:
            raise NotRun("port", f"{name} tidak membuka port 3306 ke host")
        if labels.get("lumbung.managed") == "1" or labels.get("lumbung.promoted") == "1":
            self._env_file(name, labels)                    # harus ada untuk Update image nanti
            self.managed = {"port": port, "image_tag": self._tag_of(image)}
            self.note = f"{name} sudah dibuat runner; hanya dicatat"
            return
        if not image.startswith("zmutclik/mariadb:lumbungdb-"):
            raise NotRun("image", f"{name} memakai {image or 'image tidak dikenal'}; hanya image zmutclik/mariadb:lumbungdb-* "
                                  "yang bisa dikelola (mode sidecar tidak)")
        code, out, err = self.run(["docker", "image", "inspect", "-f", "{{json .Config.Env}}", inspected.get("Image", "")], 60)
        if code != 0:
            raise NotRun("image", f"image container tidak terbaca: {err.strip()[-200:]}")
        try:
            image_env = set(json.loads(out.strip() or "[]") or [])
        except ValueError:
            image_env = set()
        own = [e for e in config.get("Env") or [] if e not in image_env and "=" in e]
        keys = {e.split("=", 1)[0] for e in own}
        if "LUMBUNG_TOKEN" not in keys:
            raise NotRun("environment", f"{name} tidak punya LUMBUNG_TOKEN; bukan container agen LumbungDB")
        if any("\n" in e or "\r" in e for e in own):
            raise NotRun("environment", "ada nilai environment berisi baris baru; tidak bisa ditulis ke .env")
        # Tag yang sekarang menunjuk image lain (mis. di-pull ulang) tidak dipakai: versi tidak boleh berubah diam-diam.
        code, out, _ = self.run(["docker", "image", "inspect", "-f", "{{.Id}}", image], 60)
        run_image = image if code == 0 and out.strip() == inspected.get("Image") else inspected.get("Image")
        target = os.path.join(self.config["container_dir"], name)
        os.makedirs(target, mode=0o700, exist_ok=True)
        env_path = os.path.join(target, ".env")
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(sorted(own)) + "\n")
        args = self._recreate_args(name, inspected, env_path, run_image, extra_labels=(MANAGED_LABEL,
                                                                                     f"lumbung.container={name}"))
        try:
            self._replace(name, inspected, args, "jalankan ulang dengan label runner", "tunggu MariaDB sehat")
        except NotRun:
            os.unlink(env_path)
            raise
        self.managed = {"port": port, "image_tag": self._tag_of(image)}
        self.note = f"{name} kini dikelola runner (port {port}); .env di {env_path}. Jangan jalankan docker compose di folder lama."

    # ---------- pasang ----------
    def install(self):
        info = (self.job.get("install") or {})
        name, project = info.get("name", ""), info.get("project", "")
        if not _SAFE_NAME.fullmatch(name) or name != self.job["container"] or not re.fullmatch(r"[a-z0-9_-]{1,64}", project):
            raise NotRun("tugas", "data pemasangan tidak terbaca (nama atau project)")
        for key in ("image", "token", "age_recipients", "db_name", "cpus", "memory"):
            if not _SAFE_VALUE.fullmatch(str(info.get(key, ""))):
                raise NotRun("tugas", f"nilai {key} berisi karakter yang tidak dikenal")
        port = int(info.get("port") or 0)
        if not 1 <= port <= 65535:
            raise NotRun("tugas", "port tidak valid")
        if self._labels(name) is not None:
            raise NotRun("nama container", f"container bernama {name} sudah ada di mesin ini")
        for volume in (f"{project}_data", f"{project}_backup"):
            code, _, _ = self.run(["docker", "volume", "inspect", volume], 60)
            if code == 0:
                raise NotRun("volume", f"volume {volume} sudah ada di mesin ini; password baru tidak cocok dengan data lama")
        target = os.path.join(self.config["container_dir"], name)
        if os.path.exists(os.path.join(target, ".env")):
            raise NotRun("folder", f"{target}/.env sudah ada")
        self.progress(f"unduh image {info['image']}")
        code, _, err = self.run(["docker", "pull", info["image"]], 3600)
        if code != 0:
            raise NotRun("unduh image", err.strip()[-300:])
        os.makedirs(target, mode=0o700, exist_ok=True)
        env_path = os.path.join(target, ".env")
        lines = (
            f"LUMBUNG_URL={self.client.base_url}", f"LUMBUNG_TOKEN={info['token']}",
            f"LUMBUNG_AGE_RECIPIENTS={info['age_recipients']}",
            f"MARIADB_ROOT_PASSWORD={random_text()}", f"MARIADB_DATABASE={info['db_name']}",
            f"MARIADB_USER={random_user('app')}", f"MARIADB_PASSWORD={random_text()}",
            f"BACKUP_USER={random_user('backup')}", f"BACKUP_PASS={random_text()}",
            "BACKUP_CRON=", "DUMP_CRON=", "BINLOG_CRON=", "BINLOG_UNSENT_MAX_DAYS=3", "MARIADB_AUTO_UPGRADE=1", "")
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines))
        self.progress("jalankan container")
        code, _, err = self.run([
            "docker", "run", "-d", "--name", name, "--hostname", name, "--restart", "always",
            "--label", MANAGED_LABEL, "--label", f"lumbung.container={name}", "--env-file", env_path,
            "-p", f"{port}:3306", "--memory", info["memory"], "--cpus", info["cpus"],
            "-v", f"{project}_data:/var/lib/mysql", "-v", f"{project}_backup:/backup",
            "-v", "/etc/hostname:/lumbung/server/hostname:ro", "-v", "/etc/machine-id:/lumbung/server/machine-id:ro",
            "--health-cmd", "healthcheck.sh --connect --innodb_initialized", "--health-start-period", "60s",
            "--health-interval", "10s", "--health-timeout", "5s", "--health-retries", "3",
            info["image"]], 600)
        if code != 0:
            # Container yang gagal dibuat (mis. port terpakai) dibuang beserta volume barunya, supaya bisa diulang.
            self.run(["docker", "rm", "-f", name], 120)
            self.run(["docker", "volume", "rm", f"{project}_data", f"{project}_backup"], 120)
            os.unlink(env_path)
            raise NotRun("jalankan container", err.strip()[-300:])
        self.progress("tunggu MariaDB sehat")
        for _ in range(max(self.config["ready_seconds"], 600) // 5):
            code, out, _ = self.run(["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                                     name], 60)
            health = out.strip() if code == 0 else ""
            if health == "healthy":
                self.note = f"{name} berjalan di port {port}; user dan password di {env_path} pada mesin runner"
                return
            if health == "unhealthy":
                break
            time.sleep(5)
        _, logs, err = self.run(["docker", "logs", "--tail", "15", name])
        raise Failed("MariaDB tidak sehat", (logs + err).strip()[-LOG_LIMIT:])

    def execute(self):
        kind = self.job["kind"]
        try:
            if kind == "container_install":
                self.install()
            elif kind == "container_update":
                self.update_image()
            elif kind == "container_adopt":
                self.adopt()
            elif kind in CONTROL_KINDS:
                self.power(kind.removeprefix("container_"))
            else:
                raise NotRun("tugas", f"jenis tugas {kind} tidak dikenal")
            return "Lolos"
        except Failed as exc:
            self.note = f"{exc.step}: {exc.message}"[:500]
            return "Gagal"
        except NotRun as exc:
            self.note = f"{exc.step}: {exc.message}"[:500]
            return "Tidak terlaksana"


def host_metrics(previous=None):
    """CPU dan memori seluruh mesin dari /proc (di container tanpa lxcfs, /proc/stat dan /proc/meminfo milik host).
    previous: (total, idle) dari panggilan sebelumnya; CPU dihitung dari selisihnya."""
    found = {"cpu_count": os.cpu_count()}
    sample = None
    try:
        with open("/proc/stat") as handle:
            fields = [int(v) for v in handle.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        sample = (sum(fields[:8]), idle)
        if previous and sample[0] > previous[0]:
            busy = 1 - (sample[1] - previous[1]) / (sample[0] - previous[0])
            found["cpu_percent"] = round(max(0.0, min(1.0, busy)) * 100, 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        meminfo = {}
        with open("/proc/meminfo") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.split()[0]) * 1024
        found["mem_total"] = meminfo["MemTotal"]
        found["mem_available"] = meminfo.get("MemAvailable", meminfo.get("MemFree"))
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        found["load1"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    return found, sample


def managed_containers(runner=run):
    """Status container yang dipasang atau dinaikkan lewat runner di mesin ini."""
    found = []
    for label in (MANAGED_LABEL, "lumbung.promoted=1"):
        code, out, _ = runner(["docker", "ps", "-a", "--filter", f"label={label}", "--format",
                               "{{.Names}}\t{{.State}}\t{{.Status}}\t{{.Image}}"], 60)
        for line in out.splitlines() if code == 0 else []:
            name, state, status, image = (line.split("\t") + ["", "", ""])[:4]
            if name and all(name != f["name"] for f in found):
                found.append({"name": name[:64], "state": state[:20], "status": status[:100], "image": image[:200]})
    return found[:200]


def _binlog_number(name):
    if not name:
        return None
    digits = name.rsplit(".", 1)[-1]
    return int(digits) if digits.isdigit() else None


class Shutdown:
    """docker stop mengirim SIGTERM. Uji restore dan unduhan boleh langsung berhenti: sisanya dibersihkan saat start
    dan LumbungDB menandai tugasnya tidak selesai. Mengganti database cadangan dan naikkan ke production tidak boleh
    terputus di tengah, jadi sinyal ditahan sampai langkah itu selesai; runner berhenti sesudah hasilnya terkirim."""

    def __init__(self):
        self.event = threading.Event()
        self.critical = 0

    def handle(self, signum, frame):
        self.event.set()
        if self.critical:
            log("diminta berhenti; menunggu langkah yang sedang berjalan selesai")
            return
        raise SystemExit(0)

    @contextlib.contextmanager
    def guard(self):
        self.critical += 1
        try:
            yield
        finally:
            self.critical -= 1


SHUTDOWN = Shutdown()


def runner_version():
    """Versi ditulis ke image saat build. RUNNER_VERSION hanya untuk menjalankan runner.py di luar image."""
    try:
        with open(VERSION_FILE) as handle:
            version = handle.readline().strip()[:40]
    except OSError:
        version = ""
    return version or os.environ.get("RUNNER_VERSION", "dev")


def cleanup_leftovers(config, runner=run):
    """Sisa container dan folder dari runner yang mati di tengah tugas."""
    code, out, _ = runner(["docker", "ps", "-aq", "--filter", f"label={LABEL}"])
    ids = out.split() if code == 0 else []
    if ids:
        runner(["docker", "rm", "-f", "-v", *ids])
    scratch = config["scratch_dir"]
    for entry in os.listdir(scratch) if os.path.isdir(scratch) else []:
        if entry.startswith("job-"):
            purge_dir(os.path.join(scratch, entry), config, runner)


class Runner:
    def __init__(self, config, client, runner=run):
        self.config, self.client, self.run = config, client, runner
        self.stop = SHUTDOWN.event
        self.pending_result = None       # hasil yang belum terkirim karena LumbungDB tidak terjangkau
        self.pending_control = None      # sama, untuk jalur kontrol
        self.cpu_sample = None

    @staticmethod
    def _read_first_line(path):
        try:
            with open(path) as handle:
                return handle.readline().strip()[:255] or None
        except OSError:
            return None

    def heartbeat(self):
        try:
            usage = shutil.disk_usage(self.config["scratch_dir"])
            total, free = usage.total, usage.free
        except OSError:
            total = free = None
        if self.cpu_sample is None:
            _, self.cpu_sample = host_metrics()
            self.stop.wait(1)
        metrics, self.cpu_sample = host_metrics(self.cpu_sample)
        payload = {"server": self._read_first_line(f"{SERVER_DIR}/hostname"),
                   "machine_id": self._read_first_line(f"{SERVER_DIR}/machine-id"),
                   "version": self.config["version"], "scratch_total": total, "scratch_free": free,
                   **metrics, "containers": managed_containers(self.run)}
        return self.client.json("POST", "/api/runner/heartbeat", payload)

    def control_once(self):
        """Jalur kontrol: pasang container dan start/stop/restart. Terpisah dari uji restore, jadi stop container tidak
        menunggu uji yang berjam-jam. True bila ada tugas yang dikerjakan."""
        if self.pending_control:
            job_id, body = self.pending_control
            code, _ = self.client.json("POST", f"/api/runner/jobs/{job_id}/result", body)
            if code not in (200, 403, 404, 409):
                return False
            self.pending_control = None
        code, reply = self.client.json("POST", "/api/runner/jobs/claim?lane=control")
        job = reply.get("job") if code == 200 else None
        if not job:
            return False
        log(f"tugas kontrol {job['id']}: {job['kind']} {job['container']}")

        def progress(step):
            self.client.json("POST", f"/api/runner/jobs/{job['id']}/progress", {"step": step[:200]})

        worker = ContainerWorker(self.config, self.client, job, self.run, progress)
        try:
            if job["kind"] in ("container_install", "container_update", "container_adopt"):
                with SHUTDOWN.guard():
                    status = worker.execute()
            else:
                status = worker.execute()
        except Exception as exc:     # kesalahan runner sendiri tidak boleh menghentikan loop
            status, worker.note = "Tidak terlaksana", f"{type(exc).__name__}: {exc}"[:500]
        body = {"status": status, "note": worker.note, "results": []}
        if status == "Lolos" and worker.managed:
            body["managed"] = worker.managed
        self.pending_control = (job["id"], body)
        code, _ = self.client.json("POST", f"/api/runner/jobs/{job['id']}/result", self.pending_control[1])
        if code in (200, 403, 404, 409):
            self.pending_control = None
        log(f"tugas kontrol {job['id']} selesai: {status}")
        return True

    def control_loop(self):
        while not self.stop.is_set():
            try:
                if not self.control_once():
                    self.stop.wait(self.config["poll_seconds"])
            except (OSError, http.client.HTTPException) as exc:
                log(f"LumbungDB tidak terjangkau (kontrol): {type(exc).__name__}")
                self.stop.wait(self.config["poll_seconds"])

    def flush_result(self):
        if not self.pending_result:
            return
        job_id, body = self.pending_result
        code, _ = self.client.json("POST", f"/api/runner/jobs/{job_id}/result", body)
        if code in (200, 403, 404, 409):      # 409: tugas sudah ditandai tidak selesai oleh LumbungDB
            self.pending_result = None

    def work_once(self):
        """True bila ada tugas yang dikerjakan."""
        self.flush_result()
        if self.pending_result:
            return False
        code, reply = self.client.json("POST", "/api/runner/jobs/claim")
        job = reply.get("job") if code == 200 else None
        if not job:
            return False
        log(f"tugas {job['id']} untuk {job['container']}: {len(job['files'])} file")

        def progress(step):
            self.client.json("POST", f"/api/runner/jobs/{job['id']}/progress", {"step": step[:200]})

        try:
            if job["kind"] in ("standby_full", "standby_apply", "standby_remove", "standby_promote"):
                worker = StandbyRunner(self.config, self.client, job, self.run, progress)
                status, results = worker.execute()
                note, extra = worker.note, worker.extra
            else:
                status, results = Restorer(self.config, self.client, job, self.run, progress).execute()
                note, extra = None, {}
        except Exception as exc:     # kesalahan runner sendiri tidak boleh menghentikan loop
            status, results, note, extra = "Tidak terlaksana", [], f"{type(exc).__name__}: {exc}"[:500], {}
        self.pending_result = (job["id"], {"status": status, "note": note, "results": results, **extra})
        self.flush_result()
        log(f"tugas {job['id']} selesai: {status}")
        return True

    def loop(self):
        cleanup_leftovers(self.config, self.run)
        control = threading.Thread(target=self.control_loop, name="kontrol", daemon=True)
        control.start()
        try:
            self._main_loop()
        finally:
            # Pemasangan yang sedang berjalan ditunggu selesai (SHUTDOWN.guard), sisanya berhenti di putaran berikutnya.
            self.stop.set()
            control.join()

    def _main_loop(self):
        last_beat = 0
        while not self.stop.is_set():
            try:
                if time.time() - last_beat >= 60:
                    self.heartbeat()
                    last_beat = time.time()
                if not self.work_once():
                    self.stop.wait(self.config["poll_seconds"])
            except (OSError, http.client.HTTPException) as exc:
                log(f"LumbungDB tidak terjangkau: {type(exc).__name__}")
                self.stop.wait(self.config["poll_seconds"])


def load_config():
    def int_env(name, default):
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return default

    return {
        "scratch_dir": os.environ.get("SCRATCH_DIR", ""),
        "age_identity": os.environ.get("AGE_IDENTITY_FILE", ""),
        "image": os.environ.get("MARIADB_IMAGE", ""),
        "version": runner_version(),
        "poll_seconds": max(1, int_env("POLL_SECONDS", 15)),
        "ready_seconds": max(5, int_env("READY_SECONDS", 180)),
        "standby_port_base": int_env("STANDBY_PORT_BASE", DEFAULT_STANDBY_PORT),
        "standby_bind": os.environ.get("STANDBY_BIND_ADDRESS", "127.0.0.1"),
        "promote_dir": os.environ.get("PROMOTE_DIR") or os.path.join(os.environ.get("SCRATCH_DIR", ""), "promoted"),
        "promote_memory": os.environ.get("PROMOTE_MEMORY", "2g"),
        "promote_cpus": os.environ.get("PROMOTE_CPUS", "2"),
        # .env container yang dipasang lewat runner (berisi password); datanya di volume Docker <project>_data
        "container_dir": os.path.join(os.environ.get("SCRATCH_DIR", ""), "containers"),
    }


def main():
    argparse.ArgumentParser(prog="lumbung-runner").parse_args()
    config = load_config()
    url, token = os.environ.get("LUMBUNG_URL", ""), os.environ.get("LUMBUNG_RUNNER_TOKEN", "")
    missing = [n for n, v in (("LUMBUNG_URL", url), ("LUMBUNG_RUNNER_TOKEN", token), ("SCRATCH_DIR", config["scratch_dir"]),
                              ("AGE_IDENTITY_FILE", config["age_identity"]), ("MARIADB_IMAGE", config["image"])) if not v]
    if missing:
        print("Wajib diisi: " + ", ".join(missing), file=sys.stderr)
        return 2
    os.makedirs(config["scratch_dir"], exist_ok=True)
    signal.signal(signal.SIGTERM, SHUTDOWN.handle)
    log(f"runner mulai, versi {config['version']}, host {socket.gethostname()}")
    Runner(config, Client(url, token)).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
