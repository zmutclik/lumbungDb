"""Program runner: unduhan yang dilanjutkan, hasil tiga jenis, dan hasil yang tertahan saat LumbungDB tidak terjangkau."""
import hashlib
import http.server
import importlib.util
import threading
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "runner" / "runner.py"


@pytest.fixture
def r():
    spec = importlib.util.spec_from_file_location("lumbung_runner_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BODY = bytes(range(256)) * 4000


class Handler(http.server.BaseHTTPRequestHandler):
    cut_first = True
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        assert self.headers["X-Runner-Token"] == "tok"
        rng = self.headers.get("Range")
        offset = int(rng.split("=")[1].rstrip("-")) if rng else 0
        Handler.seen.append(offset)
        chunk = BODY[offset:]
        self.send_response(206 if offset else 200)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        if Handler.cut_first and not offset:
            Handler.cut_first = False
            self.wfile.write(chunk[: len(chunk) // 3])      # putus di tengah jalan
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(chunk)


@pytest.fixture
def server():
    Handler.cut_first, Handler.seen = True, []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def entry(**over):
    return {"id": 7, "relpath": "fullbackup/x.age", "size": len(BODY), "sha256": hashlib.sha256(BODY).hexdigest(), **over}


def test_download_resumes_from_last_byte_and_verifies_checksum(r, server, tmp_path):
    client = r.Client(server, "tok")
    dest = tmp_path / "f"
    client.download(1, entry(), str(dest))
    assert dest.read_bytes() == BODY
    assert len(Handler.seen) == 2 and Handler.seen[0] == 0 and 0 < Handler.seen[1] < len(BODY)   # lanjut, bukan dari awal


def test_checksum_mismatch_is_failed_not_run_failure(r, server, tmp_path):
    Handler.cut_first = False
    with pytest.raises(r.Failed) as err:
        r.Client(server, "tok").download(1, entry(sha256="0" * 64), str(tmp_path / "f"))
    assert "rusak" in err.value.message
    assert not (tmp_path / "f").exists()


def test_unreachable_server_is_not_run(r, tmp_path, monkeypatch):
    monkeypatch.setattr(r.time, "sleep", lambda s: None)
    with pytest.raises(r.NotRun) as err:
        r.Client("http://127.0.0.1:1", "tok").download(1, entry(), str(tmp_path / "f"))
    assert not isinstance(err.value, r.Failed)


class FakeClient:
    def __init__(self, fail=None):
        self.fail, self.calls, self.job, self.reachable = fail, [], None, True

    def download(self, job_id, ent, dest):
        if self.fail and self.fail(ent):
            raise self.fail(ent)
        Path(dest).write_bytes(b"x")

    def json(self, method, path, body=None):
        if not self.reachable:
            raise OSError("putus")
        self.calls.append((path, body))
        if path.endswith("/claim"):
            job, self.job = self.job, None
            return 200, {"job": job}
        return 200, {}


def config(tmp_path):
    return {"scratch_dir": str(tmp_path), "age_identity": "/k", "image": "img", "ready_seconds": 1,
            "version": "t", "poll_seconds": 1}


def fake_run(calls, fail_on=()):
    def run(args, timeout=0, stdin_path=None):
        calls.append(args)
        text = " ".join(args)
        if args[:2] == ["docker", "logs"]:
            return 0, "", "MariaDB init process done. Ready for start up."
        for marker, result in fail_on:
            if marker in text:
                return result
        if "information_schema.tables" in text and "COUNT" not in text.split("-e")[-1][:20] and "table_schema, table_name" in text:
            return 0, "appdb\tt\n", ""
        if "SELECT 1" in text:
            return 0, "1\n", ""
        if "COUNT(*) FROM information_schema" in text:
            return 0, "1\n", ""
        if "global_priv" in text:
            return 0, "3\n", ""
        return 0, "", ""
    return run


def job(*files):
    return {"id": 5, "container": "siak-db", "files": list(files)}


FULL = {"id": 1, "kind": "fullbackup", "relpath": "f", "size": 1, "sha256": "a", "database": None, "binlog_no": None,
        "binlog_start": "mysql-bin.000001"}
DUMP = {"id": 2, "kind": "databases", "relpath": "d", "size": 1, "sha256": "a", "database": "appdb", "binlog_no": None}
USERS = {**DUMP, "id": 3, "database": "_users"}


def binlog(no, i):
    return {"id": 10 + i, "kind": "binlog", "relpath": f"b{no}", "size": 1, "sha256": "a", "database": None, "binlog_no": no}


def execute(r, tmp_path, files, client=None, calls=None, fail_on=()):
    calls = [] if calls is None else calls
    restorer = r.Restorer(config(tmp_path), client or FakeClient(), job(*files), fake_run(calls, fail_on))
    return restorer.execute(), calls


def test_all_kinds_pass_and_everything_is_cleaned(r, tmp_path):
    (status, results), calls = execute(r, tmp_path, [FULL, DUMP, USERS, binlog(1, 0), binlog(2, 1)])
    assert status == "Lolos"
    assert [x["kind"] for x in results] == ["full", "dump", "users", "binlog"]
    removed = [c for c in calls if c[:3] == ["docker", "rm", "-f"]]
    started = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
    assert len(removed) >= len(started) == 3
    assert not any("--network" in c and "none" not in c for c in started)


def test_prepare_failure_is_failed_and_other_kinds_still_run(r, tmp_path):
    fail = [("mariadb-backup --prepare", (2, "", "InnoDB corrupt"))]
    (status, results), _ = execute(r, tmp_path, [FULL, DUMP], fail_on=fail)
    assert status == "Gagal"
    by_kind = {x["kind"]: x for x in results}
    assert by_kind["full"]["status"] == "Gagal" and by_kind["full"]["failed_step"] == "prepare"
    assert by_kind["dump"]["status"] == "Lolos"          # jalur dump tetap diketahui


def test_download_trouble_is_not_run_and_corrupt_file_is_failed(r, tmp_path):
    client = FakeClient(fail=lambda e: r.NotRun("unduh", "putus") if e["id"] == 2 else None)
    (status, results), _ = execute(r, tmp_path, [FULL, DUMP], client)
    assert {x["kind"]: x["status"] for x in results} == {"full": "Lolos", "dump": "Tidak terlaksana"}
    assert status == "Tidak terlaksana"
    client = FakeClient(fail=lambda e: r.Failed("checksum", "rusak") if e["id"] == 1 else None)
    (status, _), _ = execute(r, tmp_path, [FULL], client)
    assert status == "Gagal"


def test_dump_table_count_must_match_full(r, tmp_path):
    fail = [("COUNT(*) FROM information_schema", (0, "4\n", ""))]
    (status, results), _ = execute(r, tmp_path, [FULL, DUMP], fail_on=fail)
    dump = next(x for x in results if x["kind"] == "dump")
    assert dump["status"] == "Gagal" and dump["failed_step"] == "jumlah tabel"


def test_binlog_gap_and_missing_start_detected(r, tmp_path):
    (status, results), _ = execute(r, tmp_path, [FULL, binlog(1, 0), binlog(3, 1)])
    assert next(x for x in results if x["kind"] == "binlog")["failed_step"] == "rantai putus"
    late = dict(FULL, binlog_start="mysql-bin.000001")
    (_, results), _ = execute(r, tmp_path, [late, binlog(2, 0), binlog(3, 1)])
    assert next(x for x in results if x["kind"] == "binlog")["failed_step"] == "awal rantai"


def test_scratch_too_small_is_not_run(r, tmp_path):
    huge = dict(FULL, size=10 ** 18)
    (status, results), calls = execute(r, tmp_path, [huge])
    assert status == "Tidak terlaksana" and results[0]["failed_step"] == "scratch"
    assert not [c for c in calls if c[:3] == ["docker", "run", "-d"]]


def test_runner_loop_sends_result_and_holds_it_when_server_unreachable(r, tmp_path):
    client = FakeClient()
    client.job = job(DUMP)
    runner = r.Runner(config(tmp_path), client, fake_run([]))
    client.reachable = True
    assert runner.work_once() is True
    paths = [c[0] for c in client.calls]
    assert "/api/runner/jobs/5/result" in paths and runner.pending_result is None

    client.job = job(DUMP)
    original = client.json

    def flaky(method, path, body=None):
        if path.endswith("/result"):
            raise OSError("putus")
        return original(method, path, body)

    client.json = flaky
    with pytest.raises(OSError):
        runner.work_once()
    assert runner.pending_result is not None
    client.json = original
    runner.work_once()          # hasil tertahan dikirim dulu, tidak mengambil tugas baru
    assert runner.pending_result is None
    assert [c[0] for c in client.calls].count("/api/runner/jobs/5/result") == 2


def test_leftover_containers_removed_on_start(r, tmp_path):
    (tmp_path / "job-9").mkdir()
    calls = []

    def run(args, timeout=0, stdin_path=None):
        calls.append(args)
        return (0, "abc\ndef\n", "") if args[:3] == ["docker", "ps", "-aq"] else (0, "", "")

    r.cleanup_leftovers(config(tmp_path), run)
    assert ["docker", "rm", "-f", "-v", "abc", "def"] in calls
    assert any("find /work" in " ".join(c) for c in calls)
