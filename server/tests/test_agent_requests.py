"""Agen: status permintaan saat script gagal, dan enkripsi sebelum dikirim."""
import importlib.util
import socket
import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "mariadb" / "agent"


@pytest.fixture
def agent_module(monkeypatch):
    monkeypatch.syspath_prepend(str(AGENT_DIR))
    spec = importlib.util.spec_from_file_location("lumbung_agent_under_test", AGENT_DIR / "agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "lower_priority", lambda: None)
    yield module
    sys.modules.pop("schedule", None)


def run_request(module, monkeypatch, returncode, db_up):
    reports = []

    class Stub(module.Agent):
        def __init__(self):
            pass

        def _snapshot(self):
            return {}

        def scan(self):
            return []

        def report_status(self, request_id, status, note="", log_tail=""):
            reports.append(status)

        def _record_request(self, request_id, running):
            pass

        def _send_request_files(self, request_id, items):
            return None

    monkeypatch.setattr(module.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout="", stderr=""))
    monkeypatch.setattr(module, "db_accepts_connections", lambda: db_up)
    Stub()._run_request(1, "full", None)
    return reports


@pytest.mark.parametrize(("returncode", "db_up", "expected"), [
    (1, False, ["Tidak selesai"]),
    (1, True, ["Gagal"]),
    (0, True, ["Mengirim", "Selesai"]),
    (0, False, ["Mengirim", "Selesai"]),
])
def test_final_status_follows_script_and_database(agent_module, monkeypatch, returncode, db_up, expected):
    assert run_request(agent_module, monkeypatch, returncode, db_up) == expected


def test_already_running_wins_when_database_is_down(agent_module, monkeypatch):
    code = agent_module.EXIT_ALREADY_RUNNING
    assert run_request(agent_module, monkeypatch, code, False) == ["Sudah berjalan"]


def test_socket_check_sees_listening_and_closed_socket(agent_module, tmp_path):
    path = str(tmp_path / "db.sock")
    assert agent_module.db_accepts_connections(path) is False
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen()
        assert agent_module.db_accepts_connections(path) is True
    assert agent_module.db_accepts_connections(path) is False


RECIPIENT = "age1" + "q" * 58


@pytest.mark.parametrize(("value", "expected"), [
    (RECIPIENT, [RECIPIENT]),
    (f"{RECIPIENT},{RECIPIENT}", [RECIPIENT, RECIPIENT]),
    (f"{RECIPIENT},salah", []),
    ("", []),
    (None, []),
])
def test_one_invalid_recipient_cancels_all(agent_module, value, expected):
    assert agent_module.parse_recipients(value) == expected


def sending_agent(module, tmp_path, recipients):
    source = tmp_path / "backup" / "fullbackup" / "20261030_020000.xbstream.gz"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"isi asli")

    class Recording:
        calls = []

        def upload(self, kind, relpath, path, sha256, plain_sha256, mtime, request_id=None, binlog_start=None):
            with open(path, "rb") as handle:
                self.calls.append({"relpath": relpath, "body": handle.read(), "sha256": sha256, "plain": plain_sha256})
            return 200, "stored"

    config = {"backup_dir": str(tmp_path / "backup"), "recipients": recipients, "max_file_age_days": 0}
    agent = module.Agent.__new__(module.Agent)
    client = Recording()
    agent.__dict__.update(
        config=config, client=client, backup_dir=config["backup_dir"], lock=module.threading.RLock(), sent={}, seen={},
        in_flight=set(), upload_failures={}, last_upload_ok=None, state_dir=str(tmp_path / "backup" / ".lumbung" / "agent"),
    )
    agent.sent_file = str(tmp_path / "backup" / ".lumbung" / "agent" / "sent.json")
    agent.spool_dir = str(tmp_path / "backup" / ".lumbung" / "agent" / "spool")
    return agent, client, source


def test_file_is_encrypted_before_sending_and_spool_removed(agent_module, monkeypatch, tmp_path):
    used = []

    def fake_encrypt(source, target, recipients):
        used.append(recipients)
        Path(target).write_bytes(b"age-encryption.org/v1\nsandi")

    monkeypatch.setattr(agent_module, "encrypt_file", fake_encrypt)
    agent, client, _source = sending_agent(agent_module, tmp_path, [RECIPIENT])
    item = agent.scan()[0]
    assert agent.send_file(item) == 200
    sent = client.calls[0]
    assert sent["relpath"].endswith(".gz.age")
    assert b"isi asli" not in sent["body"]
    assert sent["plain"] == agent_module.hashlib.sha256(b"isi asli").hexdigest()
    assert sent["sha256"] == agent_module.hashlib.sha256(sent["body"]).hexdigest()
    assert used == [[RECIPIENT]]
    assert list(Path(agent.spool_dir).iterdir()) == []
    assert agent.is_sent(item)


def test_nothing_sent_without_recipients(agent_module, tmp_path):
    agent, client, _source = sending_agent(agent_module, tmp_path, [])
    item = agent.scan()[0]
    assert agent.send_file(item) is None
    assert client.calls == []
    assert "LUMBUNG_AGE_RECIPIENTS" in str(agent.upload_failures)


def test_failed_encryption_sends_nothing_and_clears_spool(agent_module, monkeypatch, tmp_path):
    def broken(source, target, recipients):
        raise subprocess.CalledProcessError(1, "age")

    monkeypatch.setattr(agent_module, "encrypt_file", broken)
    agent, client, _source = sending_agent(agent_module, tmp_path, [RECIPIENT])
    assert agent.send_file(agent.scan()[0]) is None
    assert client.calls == []
    assert list(Path(agent.spool_dir).iterdir()) == []

