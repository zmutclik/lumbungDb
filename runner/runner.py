"""Runner LumbungDB: mengambil tugas uji restore, mengunduh file lewat HTTPS, memulihkannya di container sementara,
lalu melaporkan hasil. Hanya library bawaan Python. Semua alat (age, mbstream, mariadb-backup, mariadb) dijalankan di
dalam image MariaDB lewat `docker run`, jadi mesin runner hanya butuh Docker.

Hasil tugas ada tiga. "Gagal" berarti backup-nya yang bermasalah (file rusak, prepare gagal, tabel tidak terbaca).
"Tidak terlaksana" berarti runner yang bermasalah (unduhan putus, Docker error, scratch penuh): backup tidak dicap gagal."""
import argparse
import glob
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
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
            else:
                self.apply_binlogs()
            status, note = "Lolos", None
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


def _binlog_number(name):
    if not name:
        return None
    digits = name.rsplit(".", 1)[-1]
    return int(digits) if digits.isdigit() else None


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
        self.stop = threading.Event()
        self.pending_result = None       # hasil yang belum terkirim karena LumbungDB tidak terjangkau

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
        payload = {"server": self._read_first_line(f"{SERVER_DIR}/hostname"),
                   "machine_id": self._read_first_line(f"{SERVER_DIR}/machine-id"),
                   "version": self.config["version"], "scratch_total": total, "scratch_free": free}
        return self.client.json("POST", "/api/runner/heartbeat", payload)

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
            if job["kind"] in ("standby_full", "standby_apply"):
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
        "version": os.environ.get("RUNNER_VERSION", "dev"),
        "poll_seconds": max(1, int_env("POLL_SECONDS", 15)),
        "ready_seconds": max(5, int_env("READY_SECONDS", 180)),
        "standby_port_base": int_env("STANDBY_PORT_BASE", DEFAULT_STANDBY_PORT),
        "standby_bind": os.environ.get("STANDBY_BIND_ADDRESS", "127.0.0.1"),
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
    log(f"runner mulai, versi {config['version']}, host {socket.gethostname()}")
    Runner(config, Client(url, token)).loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
