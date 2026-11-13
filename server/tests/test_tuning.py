"""Tuning MariaDB dari LumbungDB: perhitungan di image, penerapan lewat helper, alur heartbeat, dan dialog dashboard."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.dependencies import registry
from app.backup_control.models import ContainerTuning, ControlLog
from app.core.database import async_session_factory
from tests.test_control import beat
from tests.test_dashboard import csrf, post_headers  # noqa: F401

AGENT_DIR = Path(__file__).resolve().parents[2] / "mariadb" / "agent"
MB = 1024 * 1024


@pytest.fixture
def tuning_lib():
    spec = importlib.util.spec_from_file_location("lumbung_tuning_under_test", AGENT_DIR / "tuning.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(**over):
    data = {"mode": "auto", "memory_mb": 0, "max_connections": 200, "disk": "ssd"}
    data.update(over)
    return data


# ---------- perhitungan di image ----------
def test_compute_matches_previous_shell_formula(tuning_lib):
    values, basis = tuning_lib.compute(inputs(), {"memory_mb": 2048, "host_memory_mb": 16000, "cpus": 2})
    assert basis == {"memory_mb": 2048, "source": "limit container", "cpus": 2}
    assert values["innodb_buffer_pool_size"] == 1280 * MB      # 65%, kelipatan 128M
    assert values["innodb_log_file_size"] == 320 * MB
    assert values["innodb_log_buffer_size"] == 16 * MB
    assert values["tmp_table_size"] == values["max_heap_table_size"] == 32 * MB
    assert values["table_open_cache"] == 2000 and values["innodb_read_io_threads"] == 4
    assert values["thread_cache_size"] == 50 and values["innodb_io_capacity"] == 2000

    values, basis = tuning_lib.compute(inputs(memory_mb=16384, disk="hdd", max_connections=500),
                                       {"memory_mb": 32768, "host_memory_mb": 64000, "cpus": 8})
    assert basis["source"] == "diatur" and values["innodb_buffer_pool_size"] == 12288 * MB
    assert values["innodb_log_buffer_size"] == 64 * MB and values["innodb_flush_neighbors"] == 1
    assert values["innodb_io_capacity"] == 200 and values["table_open_cache"] == 5000


def test_memory_above_limit_and_no_limit(tuning_lib):
    _, basis = tuning_lib.compute(inputs(memory_mb=8192), {"memory_mb": 4096, "host_memory_mb": 16000, "cpus": 2})
    assert basis == {"memory_mb": 4096, "source": "limit container", "cpus": 2}
    _, basis = tuning_lib.compute(inputs(), {"memory_mb": None, "host_memory_mb": 16000, "cpus": 2})
    assert basis["source"] == "50% RAM host" and basis["memory_mb"] == 8000
    assert tuning_lib.compute(inputs(mode="off"), {}) == (None, None)


def test_tiny_memory_keeps_minimum_buffer_pool(tuning_lib):
    values, _ = tuning_lib.compute(inputs(memory_mb=512), {"memory_mb": None, "host_memory_mb": 4000, "cpus": 1})
    assert values["innodb_buffer_pool_size"] == 128 * MB and values["innodb_log_file_size"] == 96 * MB


def test_limits_from_cgroup_v2(tuning_lib, tmp_path):
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "meminfo").write_text("MemTotal:       16384000 kB\n")
    cgroup = tmp_path / "sys" / "fs" / "cgroup"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.max").write_text(f"{2048 * MB}\n")
    (cgroup / "cpu.max").write_text("150000 100000\n")
    limits = tuning_lib.read_limits(str(tmp_path))
    assert limits["memory_mb"] == 2048 and limits["host_memory_mb"] == 16000
    assert limits["cpus"] == min(2, limits["cpus"])            # 1,5 CPU dibulatkan ke atas
    (cgroup / "memory.max").write_text("max\n")
    assert tuning_lib.read_limits(str(tmp_path))["memory_mb"] is None


@pytest.mark.parametrize(("bad", "message"), [
    (inputs(mode="turbo"), "mode"), (inputs(memory_mb=100), "memori"), (inputs(memory_mb=True), "memori"),
    (inputs(max_connections=5), "max_connections"), (inputs(max_connections="200"), "max_connections"),
    (inputs(disk="nvme"), "disk"), ("bukan objek", "objek"),
])
def test_inputs_validated(tuning_lib, bad, message):
    assert message in tuning_lib.inputs_error(bad)


def test_env_parsing(tuning_lib, capsys):
    assert tuning_lib.from_env({"DB_MEMORY": "4G", "DB_MAX_CONNECTIONS": "300", "DB_DISK": "hdd"}) == inputs(
        memory_mb=4096, max_connections=300, disk="hdd")
    assert tuning_lib.from_env({"DB_MEMORY": "100M"})["mode"] == "off"        # seperti tuning.sh lama: dilewati
    assert tuning_lib.from_env({"DB_MAX_CONNECTIONS": "x", "DB_DISK": "nvme"}) == inputs()
    assert "tidak valid" in capsys.readouterr().err


@pytest.fixture
def paths(tuning_lib, tmp_path, monkeypatch):
    for name, file in (("TUNING_FILE", "backup/tuning.json"), ("EFFECTIVE_FILE", "run/effective.json"),
                       ("CNF_FILE", "conf.d/zz-tuning.cnf")):
        monkeypatch.setattr(tuning_lib, name, str(tmp_path / file))
    monkeypatch.setattr(tuning_lib, "read_limits", lambda root="": {"memory_mb": 2048, "host_memory_mb": 16000,
                                                                     "cpus": 2})
    return tmp_path


def test_apply_saves_then_sets_live_through_procedure_only(tuning_lib, paths):
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    assert tuning_lib.apply(4, inputs(max_connections=300), runner) is None
    assert json.loads((paths / "backup/tuning.json").read_text()) == {"version": 4, **inputs(max_connections=300)}
    cnf = (paths / "conf.d/zz-tuning.cnf").read_text()
    assert "max_connections = 300" in cnf and "innodb_buffer_pool_size = 1152M" in cnf    # 2 MB per koneksi
    statements = [args[-1] for args in calls]
    assert all(args[:2] == ["gosu", "lumbung_tuning"] for args in calls)
    assert all(s.startswith("CALL mysql.lumbung_set_global('") for s in statements)
    assert "CALL mysql.lumbung_set_global('max_connections', 300)" in statements
    assert not any("innodb_log_buffer_size" in s for s in statements)       # hanya saat restart
    effective = json.loads((paths / "run/effective.json").read_text())
    assert effective["source"] == "lumbung" and effective["version"] == 4 and effective["live_note"] is None


def test_apply_without_tuning_account_is_saved_for_restart(tuning_lib, paths):
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "ERROR 1045 (28000): Access denied for user 'lumbung_tuning'")

    note = tuning_lib.apply(2, inputs(), runner)
    assert "akun lumbung_tuning belum ada" in note
    assert (paths / "backup/tuning.json").exists() and (paths / "conf.d/zz-tuning.cnf").exists()
    assert json.loads((paths / "run/effective.json").read_text())["live_note"] == note


def test_apply_partial_failure_and_off_mode(tuning_lib, paths):
    def runner(args, **kwargs):
        failed = "innodb_log_file_size" in args[-1]
        return subprocess.CompletedProcess(args, 1 if failed else 0, "", "ERROR 1210: gagal" if failed else "")

    assert tuning_lib.apply(3, inputs(), runner) == "berlaku setelah restart: innodb_log_file_size"
    assert tuning_lib.apply(4, inputs(mode="off"), runner) == "mode off berlaku setelah restart"
    assert not (paths / "conf.d/zz-tuning.cnf").exists()


def test_install_prefers_saved_tuning_over_env(tuning_lib, paths):
    (paths / "backup").mkdir()
    (paths / "backup/tuning.json").write_text(json.dumps({"version": 7, **inputs(max_connections=120)}))
    tuning_lib.install({"DB_MAX_CONNECTIONS": "500"})
    effective = json.loads((paths / "run/effective.json").read_text())
    assert effective["source"] == "lumbung" and effective["max_connections"] == 120
    (paths / "backup/tuning.json").write_text("rusak")
    tuning_lib.install({"DB_MAX_CONNECTIONS": "500"})
    effective = json.loads((paths / "run/effective.json").read_text())
    assert effective["source"] == "env" and effective["max_connections"] == 500


# ---------- agen ----------
@pytest.fixture
def agent_module(monkeypatch):
    monkeypatch.syspath_prepend(str(AGENT_DIR))
    spec = importlib.util.spec_from_file_location("lumbung_agent_tuning_test", AGENT_DIR / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in ("schedule", "tuning", "metrics"):
        sys.modules.pop(name, None)


def test_agent_passes_only_validated_values_to_helper(agent_module, monkeypatch):
    calls = []
    monkeypatch.setattr(agent_module.subprocess, "run",
                        lambda args, **k: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    agent = agent_module.Agent.__new__(agent_module.Agent)
    agent.tuning_error = None
    agent.handle_tuning({"version": 3, **inputs(memory_mb=4096)})
    assert calls == [agent_module.HELPER + ["apply-tuning", "3", "auto", "4096", "200", "ssd"]]
    agent.handle_tuning({"version": 4, **inputs(disk="ssd; rm -rf /")})
    assert len(calls) == 1 and agent.tuning_error == {"version": 4, "message": "jenis disk harus ssd atau hdd"}


# ---------- LumbungDB ----------
def reported(version=0, **over):
    data = {"version": version, "source": "env", **inputs(),
            "values": {"max_connections": 200, "innodb_buffer_pool_size": 1280 * MB},
            "basis": {"memory_mb": 2048, "source": "limit container", "cpus": 2},
            "limits": {"memory_mb": 2048, "host_memory_mb": 16000, "cpus": 2},
            "running": {"max_connections": 200, "innodb_buffer_pool_size": 1280 * MB}, "live_note": None, "error": None}
    data.update(over)
    return data


async def test_old_agent_without_tuning_cannot_be_tuned(client, csrf):  # noqa: F811
    agent = {"X-Token": registry.add("siak-db")}
    await beat(client, agent)
    page = (await client.get("/containers/siak-db")).text
    assert "belum mendukung tuning" in page and "Ubah tuning" not in page
    resp = await client.get("/containers/siak-db/tuning/edit", headers={"HX-Request": "true"})
    assert resp.status_code == 409


async def test_tuning_flow_until_applied(client, csrf):  # noqa: F811
    agent = {"X-Token": registry.add("siak-db")}
    reply = await beat(client, agent, tuning=reported())
    assert "tuning" not in reply                                      # laporan pertama menjadi nilai awal
    page = (await client.get("/containers/siak-db")).text
    assert "Dari env container" in page and "Ubah tuning" in page and "innodb_buffer_pool_size" in page

    form = {"mode": "auto", "memory_mb": "4096", "max_connections": "300", "disk": "ssd"}
    invalid = await client.put("/containers/siak-db/tuning", data=form, headers=post_headers(csrf))
    assert invalid.status_code == 422 and "Melebihi limit memori container" in invalid.text
    assert invalid.headers["hx-retarget"] == "#tuning-form"
    form.update(memory_mb="", max_connections="300")
    assert (await client.put("/containers/siak-db/tuning", data=form, headers=post_headers(csrf))).status_code == 200

    reply = await beat(client, agent, tuning=reported())
    assert reply["tuning"] == {"version": 1, **inputs(max_connections=300)}
    assert "Menunggu diterapkan: versi 1" in (await client.get("/containers/siak-db")).text

    reply = await beat(client, agent, tuning=reported(1, source="lumbung", max_connections=300,
                                                      live_note="berlaku setelah restart: innodb_log_file_size"))
    assert "tuning" not in reply
    page = (await client.get("/containers/siak-db")).text
    assert "Diatur LumbungDB" in page and "sebagian belum berlaku" in page
    async with async_session_factory() as db:
        log = (await db.scalars(select(ControlLog).where(ControlLog.action == "Ubah tuning"))).one()
    assert log.detail == "Max connections: 200 → 300"


async def test_rejected_tuning_not_resent(client, csrf):  # noqa: F811
    agent = {"X-Token": registry.add("siak-db")}
    await beat(client, agent, tuning=reported())
    form = {"mode": "auto", "memory_mb": "1024", "max_connections": "200", "disk": "hdd"}
    await client.put("/containers/siak-db/tuning", data=form, headers=post_headers(csrf))
    reply = await beat(client, agent, tuning=reported(error={"version": 1, "message": "helper gagal: TimeoutExpired"}))
    assert "tuning" not in reply
    assert "Agen menolak tuning versi 1" in (await client.get("/containers/siak-db")).text
    async with async_session_factory() as db:
        row = await db.get(ContainerTuning, "siak-db")
    assert row.error_version == 1 and row.requested_disk == "hdd"


async def test_odd_tuning_report_never_breaks_heartbeat(client, csrf):  # noqa: F811
    agent = {"X-Token": registry.add("siak-db")}
    await beat(client, agent, tuning=reported(mode="xx", live_note="x" * 2000))
    async with async_session_factory() as db:
        assert await db.get(ContainerTuning, "siak-db") is None       # nilai tidak valid tidak diadopsi
