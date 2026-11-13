"""Penanda agen dan runner yang perlu update. Versi lama tetap dilayani; hanya ditandai."""
import pytest

from app.agents.dependencies import registry as agents
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.versions import agent_update, runner_update
from app.runners.dependencies import registry as runners
from app.runners.models import RunnerHeartbeat
from tests.test_control import beat
from tests.test_dashboard import csrf  # noqa: F401
from tests.test_runner_program import r  # noqa: F401
from tests.test_runners import clean_files  # noqa: F401


@pytest.fixture
def latest(monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_TAG_DEFAULT", "12.3-a3")
    monkeypatch.setattr(settings, "RUNNER_VERSION_LATEST", "r3")


def test_agent_update_keeps_container_mariadb_version(latest):
    assert agent_update("11.4-a1") == "11.4-a3"
    assert agent_update("12.3-a2") == "12.3-a3"
    for unknown in (None, "", "dev", "12.3", "12.3-a3", "12.3-a4", "12.3-a10"):
        assert agent_update(unknown) is None


def test_runner_update(latest):
    assert runner_update("r1") == "r3"
    for unknown in (None, "dev", "1.0", "r3", "r12"):
        assert runner_update(unknown) is None


async def test_old_agent_accepted_and_flagged(client, csrf, latest):  # noqa: F811
    agent = {"X-Token": agents.add("siak-db")}
    await beat(client, agent, agent_version="11.4-a1")
    detail = (await client.get("/containers/siak-db")).text
    assert "Perlu update" in detail and "<code>11.4-a3</code>" in detail
    assert "Agen perlu update" in (await client.get("/containers")).text

    await beat(client, agent, agent_version="11.4-a3")
    assert "Perlu update" not in (await client.get("/containers/siak-db")).text
    assert "Agen perlu update" not in (await client.get("/containers")).text


async def test_old_runner_flagged(client, csrf, latest):  # noqa: F811
    runners.add("runner-1")
    async with async_session_factory() as db, db.begin():
        db.add(RunnerHeartbeat(runner="runner-1", server="vm-uji", machine_id="b" * 32, version="r2"))
    assert "Perlu update ke r3" in (await client.get("/runners")).text
    async with async_session_factory() as db, db.begin():
        (await db.get(RunnerHeartbeat, "runner-1")).version = "r3"
    assert "Perlu update ke" not in (await client.get("/runners")).text


def test_runner_version_from_image_file(r, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RUNNER_VERSION", "dari-env")
    monkeypatch.setattr(r, "VERSION_FILE", str(tmp_path / "VERSION"))
    assert r.runner_version() == "dari-env"          # di luar image
    (tmp_path / "VERSION").write_text("r2\n")
    assert r.runner_version() == "r2"


def test_sigterm_waits_for_critical_step_only(r):  # noqa: F811
    shutdown = r.Shutdown()
    with shutdown.guard():
        shutdown.handle(15, None)                     # ditahan: langkah ini tidak boleh terputus
        assert shutdown.event.is_set()
    with pytest.raises(SystemExit):
        shutdown.handle(15, None)                     # di luar langkah kritis langsung berhenti


def test_promote_and_swap_run_under_guard(r):  # noqa: F811
    source = open(r.__file__).read()
    assert "with SHUTDOWN.guard():\n                    self.promote()" in source
    assert "with SHUTDOWN.guard():\n            port = self._swap(" in source
