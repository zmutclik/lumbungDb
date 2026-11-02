"""Agen LumbungDB: mengirim file backup dan heartbeat, menerapkan jadwal dan permintaan backup.

Berjalan sebagai user `lumbung` di dalam image, tanpa port, hanya memakai library bawaan Python.
LumbungDB hanya bisa mengirim jadwal dan jenis backup, tidak pernah teks perintah.
"""
import argparse
import ctypes
import glob
import hashlib
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

sys.path.insert(0, "/opt/lumbung")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schedule as schedule_lib  # noqa: E402

HELPER = ["sudo", "-n", "/usr/local/sbin/lumbung-helper"]
EXIT_ALREADY_RUNNING = 75
CHUNK = 1024 * 1024
REQUEST_UPLOAD_LIMIT_SECONDS = 30 * 60
DATABASE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HTTP_TIMEOUT = 120
SERVER_DIR = "/lumbung/server"


def log(message):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} agen: {message}", flush=True)


def lower_priority():
    """Upload memakai jatah CPU dan disk yang sama dengan MariaDB, jadi berjalan paling rendah. Hanya thread ini."""
    tid = threading.get_native_id()
    try:
        os.setpriority(os.PRIO_PROCESS, tid, 19)
        libc = ctypes.CDLL(None, use_errno=True)
        # ioprio_set(IOPRIO_WHO_PROCESS, tid, IOPRIO_CLASS_IDLE << 13), nomor syscall x86_64 dan arm64
        syscall = {"x86_64": 251, "aarch64": 30}.get(os.uname().machine)
        if syscall:
            libc.syscall(syscall, 1, tid, 3 << 13)
    except OSError:
        pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class Client:
    def __init__(self, url, token):
        parsed = urllib.parse.urlsplit(url.rstrip("/"))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("LUMBUNG_URL tidak valid")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port
        self.prefix = parsed.path
        self.token = token

    def _connection(self):
        if self.scheme == "https":
            # Sertifikat proxy diperiksa dengan daftar CA bawaan; tidak pernah dilewati.
            return http.client.HTTPSConnection(
                self.host, self.port, timeout=HTTP_TIMEOUT, context=ssl.create_default_context()
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=HTTP_TIMEOUT)

    def json(self, method, path, body=None):
        conn = self._connection()
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"X-Token": self.token, "Content-Type": "application/json"}
            conn.request(method, self.prefix + path, body=payload, headers=headers)
            response = conn.getresponse()
            data = response.read()
            parsed = None
            if data:
                try:
                    parsed = json.loads(data)
                except ValueError:
                    pass
            return response.status, parsed
        finally:
            conn.close()

    def upload(self, kind, relpath, path, sha256, stat, request_id=None, binlog_start=None):
        conn = self._connection()
        try:
            headers = {
                "X-Token": self.token,
                "X-SHA256": sha256,
                "X-File-Mtime": str(int(stat.st_mtime)),
                "Content-Length": str(stat.st_size),
                "Content-Type": "application/octet-stream",
            }
            if request_id is not None:
                headers["X-Backup-Request"] = str(request_id)
            if binlog_start:
                headers["X-Binlog-Start"] = binlog_start
            quoted = urllib.parse.quote(relpath)
            with open(path, "rb") as handle:
                conn.request("PUT", f"{self.prefix}/api/upload/{kind}/{quoted}", body=handle, headers=headers)
                response = conn.getresponse()
            data = response.read()
            try:
                result = json.loads(data).get("result") if data else None
            except (ValueError, AttributeError):
                result = None
            return response.status, result
        finally:
            conn.close()


class Agent:
    def __init__(self, config, client):
        self.config = config
        self.client = client
        self.backup_dir = config["backup_dir"]
        self.state_dir = os.path.join(self.backup_dir, ".lumbung", "agent")
        self.sent_file = os.path.join(self.state_dir, "sent.json")
        self.request_file = os.path.join(self.state_dir, "requests.json")
        self.lock = threading.RLock()
        self.sent = self._read_json(self.sent_file, {})
        self.seen = {}
        self.in_flight = set()
        self.outbox = []
        self.schedule_error = None
        self.active_requests = {}
        self.pending = {"count": 0, "bytes": 0}
        self.upload_failures = {}
        self.last_upload_ok = None
        self.mariadb_version = self._mariadb_version()
        self.stop = threading.Event()

    # ---------- berkas status ----------
    @staticmethod
    def _read_json(path, fallback):
        try:
            with open(path) as handle:
                value = json.load(handle)
            return value if isinstance(value, type(fallback)) else fallback
        except (OSError, ValueError):
            return fallback

    def _write_json(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except OSError as exc:  # mis. volume read-only saat --drain
            log(f"gagal menulis {os.path.basename(path)}: {exc}")

    # ---------- pemindaian file ----------
    def scan(self):
        found = []
        patterns = (
            ("fullbackup", "fullbackup/*.gz"),
            ("databases", "databases/*/*.gz"),
            ("binlog", "binlog/*.gz"),
        )
        max_days = self.config["max_file_age_days"]
        oldest = time.time() - max_days * 86400 if max_days > 0 else None
        for kind, pattern in patterns:
            for path in glob.glob(os.path.join(self.backup_dir, pattern)):
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if oldest is not None and stat.st_mtime < oldest:
                    continue
                relative = os.path.relpath(path, self.backup_dir)
                found.append(
                    {
                        "kind": kind,
                        "key": relative,
                        "relpath": relative.split("/", 1)[1],
                        "path": path,
                        "stat": stat,
                    }
                )
        return found

    def is_sent(self, item):
        record = self.sent.get(item["key"])
        return (
            isinstance(record, dict)
            and record.get("size") == item["stat"].st_size
            and record.get("mtime") == int(item["stat"].st_mtime)
        )

    def mark_sent(self, item):
        with self.lock:
            self.sent[item["key"]] = {"size": item["stat"].st_size, "mtime": int(item["stat"].st_mtime)}
            self._write_json(self.sent_file, self.sent)

    def forget_missing(self, found):
        keys = {item["key"] for item in found}
        with self.lock:
            gone = [key for key in self.sent if key not in keys]
            if gone and not self.config["max_file_age_days"]:
                for key in gone:
                    del self.sent[key]
                self._write_json(self.sent_file, self.sent)

    def binlog_start_for(self, item):
        if item["kind"] != "fullbackup":
            return None
        try:
            with open(item["path"] + ".info") as handle:
                for line in handle:
                    if line.startswith("BINLOG_START="):
                        value = line.split("=", 1)[1].strip()
                        return value if re.fullmatch(r"[A-Za-z0-9_-]+\.[0-9]+", value) else None
        except OSError:
            pass
        return None

    def send_file(self, item, request_id=None):
        """Mengembalikan kode HTTP, atau None bila koneksi gagal. Satu file gagal tidak menghentikan file lain."""
        key = item["key"]
        with self.lock:
            if key in self.in_flight:
                return None
            self.in_flight.add(key)
        code = None
        try:
            digest = sha256_file(item["path"])
            stat = os.stat(item["path"])
            if (stat.st_size, int(stat.st_mtime)) != (item["stat"].st_size, int(item["stat"].st_mtime)):
                return None  # masih berubah saat dibaca; dicoba lagi di putaran berikutnya
            code, _result = self.client.upload(
                item["kind"], item["relpath"], item["path"], digest, stat, request_id, self.binlog_start_for(item)
            )
            if code == 200:
                self.mark_sent(item)
                with self.lock:
                    self.upload_failures.pop(key, None)
                    self.last_upload_ok = int(time.time())
                return code
            self._note_failure(key, f"HTTP {code}", code)
            return code
        except (OSError, http.client.HTTPException) as exc:
            self._note_failure(key, type(exc).__name__, None)
            return None
        finally:
            with self.lock:
                self.in_flight.discard(key)

    def _note_failure(self, key, message, code):
        with self.lock:
            record = self.upload_failures.setdefault(key, {"first_at": int(time.time()), "tries": 0})
            record["tries"] += 1
            record["message"] = message
            record["code"] = code

    def upload_round(self, drain=False):
        found = self.scan()
        self.forget_missing(found)
        now = time.time()
        waiting, ready = [], []
        live_keys = set()
        for item in found:
            live_keys.add(item["key"])
            if self.is_sent(item):
                continue
            waiting.append(item)
            state = (item["stat"].st_size, item["stat"].st_mtime)
            previous = self.seen.get(item["key"])
            if previous is None or previous[0] != state:
                self.seen[item["key"]] = (state, now)
                if not drain:
                    continue
            elif now - previous[1] < self.config["stable_seconds"] and not drain:
                continue
            ready.append(item)
        for key in [k for k in self.seen if k not in live_keys]:
            del self.seen[key]
        with self.lock:
            for key in [k for k in self.upload_failures if k not in live_keys]:
                del self.upload_failures[key]
            self.pending = {"count": len(waiting), "bytes": sum(i["stat"].st_size for i in waiting)}
        failed = 0
        for item in sorted(ready, key=lambda i: i["stat"].st_mtime):
            if self.stop.is_set():
                break
            if self.send_file(item) == 200:
                with self.lock:
                    self.pending["count"] -= 1
                    self.pending["bytes"] -= item["stat"].st_size
            else:
                failed += 1
        return failed

    def upload_loop(self):
        lower_priority()
        while not self.stop.is_set():
            try:
                self.upload_round()
            except Exception as exc:  # agen tidak boleh berhenti karena satu putaran
                log(f"putaran upload gagal: {exc}")
            self.stop.wait(self.config["interval"])

    # ---------- heartbeat ----------
    @staticmethod
    def _mariadb_version():
        for binary in ("/usr/sbin/mariadbd", "mariadbd"):
            try:
                output = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            match = re.search(r"(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _read_first_line(path):
        try:
            with open(path) as handle:
                return handle.readline().strip()[:255] or None
        except OSError:
            return None

    def _log_tail(self):
        lines = []
        pattern = os.path.join(self.backup_dir, "log", "*.log")
        newest = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)[-4:]
        for path in newest:
            try:
                with open(path, errors="replace") as handle:
                    lines.extend(handle.readlines()[-20:])
            except OSError:
                continue
        timestamped = [line for line in lines if re.match(r"\d{4}-\d{2}-\d{2} ", line)]
        return [line.rstrip()[:500] for line in sorted(timestamped)[-20:]]

    def _disk(self):
        try:
            stat = os.statvfs(self.backup_dir)
            return {"total": stat.f_blocks * stat.f_frsize, "free": stat.f_bavail * stat.f_frsize}
        except OSError:
            return None

    def _counts(self):
        counts = {"fullbackup": 0, "databases": 0, "binlog": 0}
        for item in self.scan():
            counts[item["kind"]] += 1
        return counts

    def _effective_schedule(self):
        data = self._read_json(schedule_lib.EFFECTIVE_FILE, {})
        if schedule_lib.schedule_error(data):
            return None
        return {
            "version": data.get("version", 0),
            "source": data.get("source", "bawaan"),
            "full": data["full"],
            "dump": data["dump"],
            "binlog": data["binlog"],
            "error": self.schedule_error,
        }

    def heartbeat_payload(self):
        with self.lock:
            failures = sorted(self.upload_failures.items(), key=lambda kv: kv[1]["first_at"])
            pending = dict(self.pending)
            last_ok = self.last_upload_ok
        upload_error = None
        if failures:
            key, record = failures[0]
            upload_error = {"file": key, **record}
        return {
            "server": self._read_first_line(f"{SERVER_DIR}/hostname"),
            "machine_id": self._read_first_line(f"{SERVER_DIR}/machine-id"),
            "agent_version": self.config["agent_version"],
            "mariadb_version": self.mariadb_version,
            "disk": self._disk(),
            "files": self._counts(),
            "log": self._log_tail(),
            "pending": pending,
            "last_upload_ok": last_ok,
            "upload_error": upload_error,
            "schedule": self._effective_schedule(),
        }

    def flush_outbox(self):
        with self.lock:
            items, self.outbox = self.outbox, []
        remaining = []
        for item in items:
            try:
                code, _ = self.client.json("POST", f"/api/agent/requests/{item['id']}/status", item["body"])
                # 5xx diulang; 4xx berarti LumbungDB sudah menolak dan tidak perlu diulang
                if code >= 500:
                    remaining.append(item)
            except (OSError, http.client.HTTPException):
                remaining.append(item)
        if remaining:
            with self.lock:
                self.outbox = remaining + self.outbox

    def report_status(self, request_id, status, note="", log_tail=""):
        with self.lock:
            self.outbox.append({"id": request_id, "body": {"status": status, "note": note[:500], "log": log_tail[-4000:]}})
        self.flush_outbox()

    def handle_schedule(self, wanted):
        version = wanted.get("version")
        error = schedule_lib.schedule_error(wanted)
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            self.schedule_error = {"version": 0, "message": "versi jadwal tidak valid"}
            return
        if error:
            self.schedule_error = {"version": version, "message": error}
            log(f"jadwal versi {version} ditolak: {error}")
            return
        command = HELPER + ["apply-schedule", str(version)] + [wanted[k] for k in schedule_lib.KINDS]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            self.schedule_error = {"version": version, "message": f"helper gagal: {type(exc).__name__}"}
            return
        if run.returncode != 0:
            message = (run.stderr or "").strip().splitlines()[-1:] or [f"kode {run.returncode}"]
            self.schedule_error = {"version": version, "message": message[0][:300]}
            log(f"jadwal versi {version} gagal diterapkan: {message[0]}")
            return
        self.schedule_error = None
        log(f"jadwal versi {version} diterapkan")

    def heartbeat_once(self):
        self.flush_outbox()
        code, reply = self.client.json("POST", "/api/heartbeat", self.heartbeat_payload())
        if code != 200:
            log(f"heartbeat ditolak: HTTP {code}")
            return
        if not isinstance(reply, dict):
            return
        wanted = reply.get("schedule")
        if isinstance(wanted, dict):
            self.handle_schedule(wanted)
            # Laporkan hasilnya segera, tanpa menunggu putaran berikutnya.
            self.client.json("POST", "/api/heartbeat", self.heartbeat_payload())
        for request in reply.get("requests") or []:
            self.start_request(request)

    def heartbeat_loop(self):
        while not self.stop.is_set():
            try:
                self.heartbeat_once()
            except (OSError, http.client.HTTPException) as exc:
                log(f"heartbeat gagal: {type(exc).__name__}")
            except Exception as exc:
                log(f"heartbeat error: {exc}")
            self.stop.wait(self.config["interval"])

    # ---------- permintaan backup ----------
    def recover_requests(self):
        """Permintaan yang tercatat sebelum agen mati tidak diulang: paling banyak satu kali."""
        stale = self._read_json(self.request_file, {})
        for request_id in stale:
            if str(request_id).isdigit():
                self.report_status(int(request_id), "Tidak selesai", "Container restart atau agen mati saat berjalan")
        if stale:
            self._write_json(self.request_file, {})

    def _record_request(self, request_id, running):
        with self.lock:
            if running:
                self.active_requests[str(request_id)] = int(time.time())
            else:
                self.active_requests.pop(str(request_id), None)
            self._write_json(self.request_file, self.active_requests)

    def start_request(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        kind = request.get("kind") if isinstance(request, dict) else None
        database = request.get("database") if isinstance(request, dict) else None
        if not isinstance(request_id, int) or kind not in schedule_lib.KINDS:
            return
        with self.lock:
            if str(request_id) in self.active_requests:
                return
        if database is not None and (kind != "dump" or not DATABASE.fullmatch(str(database))):
            self.report_status(request_id, "Gagal", "Nama database ditolak agen")
            return
        self._record_request(request_id, True)
        try:
            code, _ = self.client.json("POST", f"/api/agent/requests/{request_id}/status", {"status": "Berjalan"})
        except (OSError, http.client.HTTPException):
            code = None
        if code != 200:
            # Tidak diambil: dibatalkan, kedaluwarsa, atau koneksi putus. Script tidak dijalankan.
            self._record_request(request_id, False)
            return
        threading.Thread(target=self._run_request, args=(request_id, kind, database), daemon=True).start()

    def _snapshot(self):
        return {i["key"]: (i["stat"].st_size, i["stat"].st_mtime) for i in self.scan()}

    def _run_request(self, request_id, kind, database):
        lower_priority()
        try:
            before = self._snapshot()
            args = HELPER + ["run-backup", kind] + ([database] if database else [])
            try:
                run = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=2 * 3600)
                returncode, output = run.returncode, (run.stdout or "") + (run.stderr or "")
            except (OSError, subprocess.SubprocessError) as exc:
                returncode, output = 1, type(exc).__name__
            if returncode == EXIT_ALREADY_RUNNING:
                self.report_status(request_id, "Sudah berjalan", "Backup jenis ini sedang berjalan dari cron")
                return
            produced = [
                item
                for item in self.scan()
                if before.get(item["key"]) != (item["stat"].st_size, item["stat"].st_mtime)
            ]
            script_failed = returncode != 0
            if produced or not script_failed:
                self.report_status(request_id, "Mengirim", "Script selesai, file sedang dikirim")
            error = self._send_request_files(request_id, produced)
            if script_failed:
                self.report_status(request_id, "Gagal", f"Script keluar dengan kode {returncode}", output)
            elif error:
                self.report_status(request_id, "Gagal", error, output)
            else:
                self.report_status(request_id, "Selesai", f"{len(produced)} file terkirim", output)
        finally:
            self._record_request(request_id, False)

    def _send_request_files(self, request_id, items):
        """Tanpa menunggu STABLE_SECONDS karena script sudah selesai. 503 dan koneksi putus diulang."""
        deadline = time.time() + self.config["request_upload_limit"]
        pending = list(items)
        while pending:
            retry = []
            for item in pending:
                try:
                    item["stat"] = os.stat(item["path"])
                except OSError:
                    continue
                if self.is_sent(item):
                    continue
                code = self.send_file(item, request_id)
                if code == 200:
                    continue
                if code is not None and 400 <= code < 500:
                    return f"{item['key']} ditolak: HTTP {code}"
                retry.append(item)
            pending = retry
            if pending:
                if time.time() >= deadline or self.stop.is_set():
                    return f"{len(pending)} file belum terkirim"
                self.stop.wait(min(self.config["interval"], 30))
        return None

    # ---------- jalan ----------
    def run(self):
        self.recover_requests()
        threads = [
            threading.Thread(target=self.heartbeat_loop, daemon=True),
            threading.Thread(target=self.upload_loop, daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def drain(self):
        """MariaDB mati: kirim semua file yang belum terkirim. Volume boleh read-only."""
        failed = self.upload_round(drain=True)
        with self.lock:
            log(f"drain selesai: {self.pending['count']} file tersisa, {failed} gagal")
        return 0 if failed == 0 else 1


def int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def load_config():
    agent_version = "dev"
    try:
        with open("/opt/lumbung/VERSION") as handle:
            agent_version = handle.read().strip() or "dev"
    except OSError:
        pass
    return {
        "backup_dir": os.environ.get("BACKUP_DIR", "/backup"),
        "interval": max(1, int_env("INTERVAL_SECONDS", 60)),
        "stable_seconds": max(0, int_env("STABLE_SECONDS", 120)),
        "max_file_age_days": max(0, int_env("MAX_FILE_AGE_DAYS", 0)),
        "request_upload_limit": REQUEST_UPLOAD_LIMIT_SECONDS,
        "agent_version": agent_version,
    }


def main():
    parser = argparse.ArgumentParser(prog="lumbung-agent")
    parser.add_argument("--drain", action="store_true", help="kirim semua file yang tertunda lalu berhenti")
    args = parser.parse_args()
    url, token = os.environ.get("LUMBUNG_URL", ""), os.environ.get("LUMBUNG_TOKEN", "")
    if not url or not token:
        print("LUMBUNG_URL dan LUMBUNG_TOKEN wajib diisi", file=sys.stderr)
        return 2
    agent = Agent(load_config(), Client(url, token))
    if args.drain:
        return agent.drain()
    log(f"mulai, versi {agent.config['agent_version']}")
    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
