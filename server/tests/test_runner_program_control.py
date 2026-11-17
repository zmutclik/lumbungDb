"""Program runner, jalur kontrol: pasang container, start/stop/restart, dan CPU/memori mesin."""
import json
from pathlib import Path

from tests.test_runner_program import FakeClient, config, r  # noqa: F401

INSTALL = {"name": "radiologi-db", "project": "radiologi-db", "port": 3310, "token": "tok-123",
           "age_recipients": "age1abc", "image": "zmutclik/mariadb:lumbungdb-12.3-a7", "db_name": "radiologi",
           "cpus": "2", "memory": "2g"}


def control_run(calls, *, exists=False, volume=False, health="healthy", fail_on=(), labels=None):
    def run(args, timeout=0, stdin_path=None):
        calls.append(args)
        text = " ".join(args)
        for marker, result in fail_on:
            if marker in text:
                return result
        if args[:2] == ["docker", "inspect"] and "Config.Labels" in text:
            if labels is not None:
                return 0, json.dumps(labels) + "\n", ""
            return (0, "{}\n", "") if exists else (1, "", "No such object")
        if args[:3] == ["docker", "volume", "inspect"]:
            return (0, "[]", "") if volume else (1, "", "no such volume")
        if args[:2] == ["docker", "inspect"] and "Health" in text:
            return 0, health + "\n", ""
        if args[:2] == ["docker", "inspect"] and "State.Status" in text:
            return 0, "running\n", ""
        return 0, "", ""
    return run


def worker(r, tmp_path, calls, kind="container_install", info=None, **kw):  # noqa: F811
    cfg = {**config(tmp_path), "container_dir": str(tmp_path / "containers"), "ready_seconds": 5}
    job = {"id": 3, "kind": kind, "container": "radiologi-db", "files": []}
    if kind == "container_install":
        job["install"] = {**INSTALL, **(info or {})}
    w = r.ContainerWorker(cfg, FakeClient(), job, control_run(calls, **kw))
    w.client.base_url = "https://lumbung.test"
    return w


def docker_runs(calls):
    return [" ".join(c) for c in calls if c[:2] == ["docker", "run"]]


def test_install_pulls_writes_env_and_starts_with_labels(r, tmp_path):  # noqa: F811
    calls = []
    w = worker(r, tmp_path, calls)
    assert w.execute() == "Lolos", w.note
    assert ["docker", "pull", INSTALL["image"]] in calls
    run, = docker_runs(calls)
    for part in ("--name radiologi-db", "-p 3310:3306", "--restart always", "--label lumbung.managed=1",
                 "-v radiologi-db_data:/var/lib/mysql", "-v radiologi-db_backup:/backup", "--memory 2g", "--cpus 2",
                 "--health-cmd healthcheck.sh --connect --innodb_initialized"):
        assert part in run
    assert run.endswith(INSTALL["image"])
    env_path = tmp_path / "containers" / "radiologi-db" / ".env"
    env = env_path.read_text()
    assert oct(env_path.stat().st_mode & 0o777) == "0o600"
    assert "LUMBUNG_TOKEN=tok-123" in env and "LUMBUNG_URL=https://lumbung.test" in env
    assert "MARIADB_DATABASE=radiologi" in env and "MARIADB_USER=app_" in env and "BACKUP_USER=backup_" in env
    root = next(line for line in env.splitlines() if line.startswith("MARIADB_ROOT_PASSWORD="))
    assert len(root.split("=", 1)[1]) == 32
    assert str(env_path) in w.note


def test_install_refuses_existing_container_or_volume(r, tmp_path):  # noqa: F811
    calls = []
    w = worker(r, tmp_path, calls, exists=True)
    assert w.execute() == "Tidak terlaksana" and "sudah ada" in w.note
    w = worker(r, tmp_path, calls, volume=True)
    assert w.execute() == "Tidak terlaksana" and "volume" in w.note
    assert not docker_runs(calls)


def test_install_rejects_unsafe_values(r, tmp_path):  # noqa: F811
    calls = []
    for bad in ({"token": "x\nEVIL=1"}, {"name": "lain-db"}, {"project": "A B"}, {"port": 0}, {"memory": "2g --privileged"}):
        w = worker(r, tmp_path, calls, info=bad)
        assert w.execute() == "Tidak terlaksana", bad
    assert not docker_runs(calls) and ["docker", "pull", INSTALL["image"]] not in calls


def test_install_run_failure_cleans_up_so_it_can_be_retried(r, tmp_path):  # noqa: F811
    calls = []
    w = worker(r, tmp_path, calls, fail_on=(("--name radiologi-db", (125, "", "port is already allocated")),))
    assert w.execute() == "Tidak terlaksana" and "port is already allocated" in w.note
    joined = [" ".join(c) for c in calls]
    assert "docker rm -f radiologi-db" in joined
    assert "docker volume rm radiologi-db_data radiologi-db_backup" in joined
    assert not (tmp_path / "containers" / "radiologi-db" / ".env").exists()


def test_unhealthy_install_is_failed_with_logs(r, tmp_path):  # noqa: F811
    calls = []
    w = worker(r, tmp_path, calls, health="unhealthy", fail_on=((" logs ", (0, "InnoDB: gagal", "")),))
    assert w.execute() == "Gagal" and "InnoDB: gagal" in w.note


def test_power_only_touches_managed_or_promoted_containers(r, tmp_path):  # noqa: F811
    calls = []
    w = worker(r, tmp_path, calls, kind="container_restart", labels={"lumbung.managed": "1"})
    assert w.execute() == "Lolos" and w.note == "radiologi-db: running"
    assert ["docker", "restart", "-t", "120", "radiologi-db"] in calls
    w = worker(r, tmp_path, calls, kind="container_stop", labels={"lumbung.promoted": "1"})
    assert w.execute() == "Lolos"
    calls.clear()
    w = worker(r, tmp_path, calls, kind="container_stop", labels={"com.docker.compose.project": "lain"})
    assert w.execute() == "Tidak terlaksana" and "tidak disentuh" in w.note
    assert not any(c[:2] == ["docker", "stop"] for c in calls)
    w = worker(r, tmp_path, calls, kind="container_start")          # tidak ada di mesin ini
    assert w.execute() == "Tidak terlaksana"


def test_host_metrics_from_proc(r):  # noqa: F811
    first, sample = r.host_metrics()
    assert first["mem_total"] > 0 and 0 <= first["mem_available"] <= first["mem_total"]
    assert "cpu_percent" not in first and sample is not None
    older = (sample[0] - 1000, sample[1] - 250)
    found, _ = r.host_metrics(older)
    assert 0 <= found["cpu_percent"] <= 100


def test_managed_containers_reads_both_labels(r):  # noqa: F811
    def run(args, timeout=0, stdin_path=None):
        if "label=lumbung.managed=1" in args:
            return 0, "radiologi-db\trunning\tUp 2 hours (healthy)\tzmutclik/mariadb:lumbungdb-12.3-a7\n", ""
        return 0, "siak-db-2\texited\tExited (0) 3 minutes ago\n", ""

    found = r.managed_containers(run)
    assert found == [{"name": "radiologi-db", "state": "running", "status": "Up 2 hours (healthy)",
                      "image": "zmutclik/mariadb:lumbungdb-12.3-a7"},
                     {"name": "siak-db-2", "state": "exited", "status": "Exited (0) 3 minutes ago", "image": ""}]


def test_control_once_claims_control_lane_and_reports(r, tmp_path):  # noqa: F811
    client = FakeClient()
    replies = {"job": {"id": 4, "kind": "container_start", "container": "radiologi-db", "files": []}}
    original = client.json

    def json_call(method, path, body=None):
        if path == "/api/runner/jobs/claim?lane=control":
            client.calls.append((path, body))
            job, replies["job"] = replies["job"], None
            return 200, {"job": job}
        return original(method, path, body)

    client.json = json_call
    calls = []
    runner = r.Runner(config(tmp_path), client, control_run(calls, labels={"lumbung.managed": "1"}))
    assert runner.control_once() is True
    assert ["docker", "start", "radiologi-db"] in calls
    result = next(body for path, body in client.calls if path == "/api/runner/jobs/4/result")
    assert result["status"] == "Lolos" and runner.pending_control is None
    assert runner.control_once() is False


def test_heartbeat_sends_host_metrics(r, tmp_path):  # noqa: F811
    client = FakeClient()
    runner = r.Runner({**config(tmp_path)}, client, lambda args, timeout=0, stdin_path=None: (0, "", ""))
    runner.cpu_sample = (1, 1)
    runner.heartbeat()
    body = client.calls[-1][1]
    assert body["mem_total"] > 0 and "cpu_percent" in body and body["containers"] == []
    assert Path("/proc/stat").exists()


INSPECT = {
    "Config": {"Hostname": "radiologi-db", "Image": "zmutclik/mariadb:lumbungdb-11.4-a5", "Cmd": ["mariadbd"],
               "Labels": {"lumbung.managed": "1", "lumbung.container": "radiologi-db", "org.opencontainers.x": "y"},
               "Env": ["MARIADB_VERSION=1:11.4.5"],
               "Healthcheck": {"Test": ["CMD-SHELL", "healthcheck.sh --connect --innodb_initialized"],
                               "Interval": 10_000_000_000, "Timeout": 5_000_000_000, "StartPeriod": 60_000_000_000,
                               "Retries": 3}},
    "HostConfig": {"RestartPolicy": {"Name": "always"}, "Memory": 2147483648, "NanoCpus": 2_000_000_000,
                   "PortBindings": {"3306/tcp": [{"HostIp": "", "HostPort": "3310"}]}},
    "Mounts": [{"Type": "volume", "Name": "radiologi-db_data", "Destination": "/var/lib/mysql", "RW": True},
               {"Type": "bind", "Source": "/etc/hostname", "Destination": "/lumbung/server/hostname", "RW": False}],
}
NEW = "zmutclik/mariadb:lumbungdb-12.3-a7"


def update_worker(r, tmp_path, calls, state="running healthy", fail_on=()):  # noqa: F811
    env = tmp_path / "containers" / "radiologi-db"
    env.mkdir(parents=True, exist_ok=True)
    (env / ".env").write_text("X=1\n")
    base = control_run(calls, labels={"lumbung.managed": "1"}, fail_on=fail_on)

    def run(args, timeout=0, stdin_path=None):
        if args == ["docker", "inspect", "radiologi-db"]:
            calls.append(args)
            return 0, json.dumps([INSPECT]), ""
        if args[:2] == ["docker", "inspect"] and "State.Status" in " ".join(args) and "Health" in " ".join(args):
            calls.append(args)
            return 0, state + "\n", ""
        return base(args, timeout, stdin_path)

    cfg = {**config(tmp_path), "container_dir": str(tmp_path / "containers"), "promote_dir": str(tmp_path / "p"),
           "ready_seconds": 5}
    job = {"id": 8, "kind": "container_update", "container": "radiologi-db", "files": [], "update": {"image": NEW}}
    return r.ContainerWorker(cfg, FakeClient(), job, run)


def test_update_recreates_with_same_settings_and_new_image(r, tmp_path):  # noqa: F811
    calls = []
    w = update_worker(r, tmp_path, calls)
    assert w.execute() == "Lolos", w.note
    joined = [" ".join(c) for c in calls]
    assert f"docker pull {NEW}" in joined
    assert joined.index("docker update --restart no radiologi-db") < joined.index("docker stop -t 120 radiologi-db")
    assert "docker rename radiologi-db radiologi-db-lama-8" in joined
    run, = docker_runs(calls)
    for part in ("--name radiologi-db", "--hostname radiologi-db", "--restart always", "-p 3310:3306",
                 "--memory 2147483648b", "--cpus 2", "-v radiologi-db_data:/var/lib/mysql",
                 "-v /etc/hostname:/lumbung/server/hostname:ro", "--label lumbung.managed=1",
                 "--health-cmd healthcheck.sh --connect --innodb_initialized", "--health-start-period 60s",
                 f"--env-file {tmp_path}/containers/radiologi-db/.env"):
        assert part in run, part
    assert run.endswith(f"{NEW} mariadbd") and "org.opencontainers" not in run and "MARIADB_VERSION" not in run
    assert "docker rm radiologi-db-lama-8" in joined                  # tanpa -v
    assert not any(j.startswith("docker rm -f -v") or j.startswith("docker volume rm") for j in joined)
    assert "11.4-a5 → " in w.note


def test_update_rolls_back_when_new_image_unhealthy(r, tmp_path):  # noqa: F811
    calls = []
    w = update_worker(r, tmp_path, calls, state="running unhealthy", fail_on=((" logs ", (0, "upgrade gagal", "")),))
    assert w.execute() == "Gagal" and "upgrade gagal" in w.note
    joined = [" ".join(c) for c in calls]
    tail = joined[joined.index("docker rm -f radiologi-db"):]
    assert tail[:4] == ["docker rm -f radiologi-db", "docker rename radiologi-db-lama-8 radiologi-db",
                        "docker update --restart always radiologi-db", "docker start radiologi-db"]
    assert "docker rm radiologi-db-lama-8" not in joined


def test_update_needs_env_file_and_valid_image(r, tmp_path):  # noqa: F811
    calls = []
    w = update_worker(r, tmp_path, calls)
    (tmp_path / "containers" / "radiologi-db" / ".env").unlink()
    assert w.execute() == "Tidak terlaksana" and ".env" in w.note
    w = update_worker(r, tmp_path, calls)
    w.job["update"]["image"] = "img; rm -rf /"
    assert w.execute() == "Tidak terlaksana"
    assert not any(c[:2] in (["docker", "stop"], ["docker", "pull"]) for c in calls)
