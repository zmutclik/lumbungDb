"""Dashboard runner: halaman Runner, token sekali tampil, dan tab Uji restore."""
import pytest
from sqlalchemy import select

from app.agents.dependencies import registry as agents
from app.core.database import async_session_factory
from app.runners.dependencies import registry as runners
from app.runners.models import ContainerSettings, Job, RestoreTest, RunnerHeartbeat
from tests.conftest import AGE
from tests.test_dashboard import csrf, post_headers  # noqa: F401
from tests.test_control import beat
from tests.test_runners import clean_files  # noqa: F401

NAME = "siak-db"


@pytest.fixture
async def world(client, csrf):  # noqa: F811
    agent = {"X-Token": agents.add(NAME)}
    await beat(client, agent)
    resp = await client.put("/api/upload/fullbackup/20261030_020000.xbstream.gz.age", content=AGE + b"x" * 50,
                            headers={**agent, "X-Binlog-Start": "mysql-bin.000001"})
    assert resp.status_code == 200
    resp = await client.put("/api/upload/databases/siak/siak-20261030_030000.sql.gz.age", content=AGE + b"y" * 50,
                            headers=agent)
    assert resp.status_code == 200, resp.text
    runners.add("runner-1")
    return csrf


def form(**over):
    data = {"runner": "runner-1", "test_mode": "week", "test_time": "03:30", "test_dow": "0", "full": "1"}
    data.update(over)
    return data


async def test_pages_render(client, world):
    for path in ("/runners", f"/containers/{NAME}/uji-restore", f"/containers/{NAME}/uji-restore/edit", "/runners/new"):
        assert (await client.get(path)).status_code == 200, path
    page = await client.get(f"/containers/{NAME}/uji-restore")
    assert "Belum ada runner uji" in page.text
    assert "Uji restore</a>" in (await client.get(f"/containers/{NAME}")).text      # tab ada di semua halaman container


async def test_non_logged_in_redirected(client):
    for path in ("/runners", f"/containers/{NAME}/uji-restore"):
        assert (await client.get(path)).status_code == 303


async def test_add_runner_shows_token_once_and_validates_name(client, world):
    bad = await client.post("/runners", data={"name": "-salah"}, headers=post_headers(world))
    assert bad.status_code == 422 and "harus diawali huruf atau angka" in bad.text
    ok = await client.post("/runners", data={"name": "runner-dc2"}, headers=post_headers(world))
    assert ok.status_code == 200 and "LUMBUNG_RUNNER_TOKEN=" in ok.text
    token = ok.text.split("LUMBUNG_RUNNER_TOKEN=")[1].split("\n")[0].strip()
    assert runners.authenticate(token) == "runner-dc2"
    assert token not in (await client.get("/runners")).text
    dup = await client.post("/runners", data={"name": "runner-dc2"}, headers=post_headers(world))
    assert dup.status_code == 422 and "sudah terdaftar" in dup.text


async def test_runner_table_shows_status_and_assignments(client, world):
    page = await client.get("/runners")
    assert "Belum terhubung" in page.text
    async with async_session_factory() as db, db.begin():
        db.add(RunnerHeartbeat(runner="runner-1", server="vm-uji", machine_id="b" * 32, version="1.0",
                               scratch_total=10 * 2 ** 30, scratch_free=4 * 2 ** 30))
    page = await client.get("/runners")
    assert "Sehat" in page.text and "vm-uji" in page.text and "4,0 GB" in page.text
    assert (await client.put(f"/containers/{NAME}/uji-restore", data=form(), headers=post_headers(world))).status_code == 200
    assert f'href="/containers/{NAME}/uji-restore">{NAME}</a>' in (await client.get("/runners")).text


async def test_disable_runner(client, world):
    assert (await client.get("/runners/runner-1/disable")).status_code == 200
    resp = await client.post("/runners/runner-1/disable", headers=post_headers(world))
    assert resp.status_code == 200 and "runner-1" not in runners.entries().active
    assert "Nonaktif" in (await client.get("/runners")).text
    assert (await client.post("/runners/runner-1/disable", headers=post_headers(world))).status_code == 404


async def test_save_settings_and_defaults_shown(client, world):
    resp = await client.put(f"/containers/{NAME}/uji-restore", data=form(test_dow="0", db="siak"),
                            headers=post_headers(world))
    assert resp.status_code == 200 and "show-toast" in resp.headers["hx-trigger"]
    async with async_session_factory() as db:
        row = await db.get(ContainerSettings, NAME)
    assert (row.test_runner, row.test_schedule, row.test_full, row.test_databases) == ("runner-1", "30 3 * * 0", True, ["siak"])
    dialog = await client.get(f"/containers/{NAME}/uji-restore/edit")
    assert 'value="runner-1" selected' in dialog.text and "03:30" in dialog.text
    page = await client.get(f"/containers/{NAME}/uji-restore")
    assert "Tiap Minggu 03:30" in page.text and "runner-1" in page.text


async def test_invalid_settings_are_rejected(client, world):
    headers = post_headers(world)
    bad_cron = await client.put(f"/containers/{NAME}/uji-restore", data=form(test_mode="custom", test_cron="bukan cron"),
                                headers=headers)
    assert bad_cron.status_code == 422 and bad_cron.headers["hx-retarget"] == "#test-settings-form"
    assert "bukan cron 5 kolom" in bad_cron.text.lower() or "cron 5 kolom" in bad_cron.text
    unknown = await client.put(f"/containers/{NAME}/uji-restore", data=form(runner="hantu"), headers=headers)
    assert unknown.status_code == 422 and "Runner tidak dikenal" in unknown.text
    no_runner = await client.put(f"/containers/{NAME}/uji-restore", data=form(runner=""), headers=headers)
    assert no_runner.status_code == 422 and "Pilih runner" in no_runner.text
    wrong_db = await client.put(f"/containers/{NAME}/uji-restore", data={**form(), "db": "tidak-ada"}, headers=headers)
    assert wrong_db.status_code == 422 and "Database tidak dikenal" in wrong_db.text
    async with async_session_factory() as db:
        assert await db.get(ContainerSettings, NAME) is None
    # mematikan jadwal tanpa runner boleh
    off = await client.put(f"/containers/{NAME}/uji-restore", data={"runner": "", "test_mode": "off"}, headers=headers)
    assert off.status_code == 200


async def test_preview_switches_fields(client, world):
    preview = await client.post(f"/containers/{NAME}/uji-restore/preview", data=form(test_mode="custom"),
                                headers=post_headers(world))
    assert 'name="test_cron"' in preview.text and 'name="test_time"' not in preview.text


async def test_run_now_needs_runner_then_creates_job(client, world):
    headers = post_headers(world)
    assert (await client.post(f"/containers/{NAME}/uji-restore/run", headers=headers)).status_code == 409
    await client.put(f"/containers/{NAME}/uji-restore", data=form(), headers=headers)
    resp = await client.post(f"/containers/{NAME}/uji-restore/run", headers=headers)
    assert resp.status_code == 200
    async with async_session_factory() as db:
        job, = (await db.scalars(select(Job))).all()
    assert (job.runner, job.status, job.requested_by) == ("runner-1", "Menunggu", "alice")
    page = await client.get(f"/containers/{NAME}/uji-restore")
    assert "oleh alice" in page.text and "Menunggu" in page.text


async def test_history_shows_results_and_escapes_logs(client, world):
    async with async_session_factory() as db, db.begin():
        job = Job(runner="runner-1", container=NAME, kind="restore_test", status="Gagal")
        db.add(job)
        await db.flush()
        db.add(RestoreTest(job_id=job.id, container=NAME, runner="runner-1", kind="full", status="Gagal",
                           failed_step="prepare", duration_seconds=12, log="<script>alert(1)</script>"))
    page = await client.get(f"/containers/{NAME}/uji-restore")
    assert "langkah: prepare" in page.text and "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text
    runners_page = await client.get("/runners")
    assert "full: Gagal (prepare)" in runners_page.text


async def test_alert_menu_shows_runner_alert(client, world):
    assert "Runner runner-1 belum mengirim heartbeat" in (await client.get("/alerts")).text


# ---------- tab Cadangan ----------
async def test_standby_tab_renders_empty_and_has_nav_tab(client, world):
    page = await client.get(f"/containers/{NAME}/cadangan")
    assert page.status_code == 200 and "Database cadangan belum dinyalakan" in page.text and "Belum ada" in page.text
    assert "/cadangan\">Cadangan</a>" in (await client.get(f"/containers/{NAME}")).text
    for path in (f"/containers/{NAME}/cadangan/edit", f"/containers/{NAME}/cadangan/restore"):
        assert (await client.get(path)).status_code == 200


def standby_form(**over):
    data = {"enabled": "1", "runner": "runner-1", "sfull_mode": "day", "sfull_time": "03:00",
            "sbinlog_mode": "mins", "sbinlog_every": "30"}
    data.update(over)
    return data


async def test_standby_settings_saved_and_shown(client, world):
    resp = await client.put(f"/containers/{NAME}/cadangan", data=standby_form(), headers=post_headers(world))
    assert resp.status_code == 200
    async with async_session_factory() as db:
        row = await db.get(ContainerSettings, NAME)
    assert (row.standby_enabled, row.standby_runner, row.standby_full_schedule, row.standby_binlog_schedule) == (
        True, "runner-1", "0 3 * * *", "*/30 * * * *")
    dialog = (await client.get(f"/containers/{NAME}/cadangan/edit")).text
    assert 'value="runner-1" selected' in dialog and 'value="03:00"' in dialog and '<option value="30" selected>' in dialog
    page = (await client.get(f"/containers/{NAME}/cadangan")).text
    assert "Tiap hari 03:00" in page and "Tiap 30 menit" in page and "belum dinyalakan" not in page


async def test_standby_settings_validation(client, world):
    headers = post_headers(world)
    no_runner = await client.put(f"/containers/{NAME}/cadangan", data=standby_form(runner=""), headers=headers)
    assert no_runner.status_code == 422 and "Pilih runner cadangan" in no_runner.text
    assert no_runner.headers["hx-retarget"] == "#standby-settings-form"
    bad = await client.put(f"/containers/{NAME}/cadangan", data=standby_form(sbinlog_mode="custom", sbinlog_cron="x"),
                           headers=headers)
    assert bad.status_code == 422 and "cron 5 kolom" in bad.text
    ghost = await client.put(f"/containers/{NAME}/cadangan", data=standby_form(runner="hantu"), headers=headers)
    assert ghost.status_code == 422
    async with async_session_factory() as db:
        assert await db.get(ContainerSettings, NAME) is None


async def test_standby_refuses_runner_on_same_server(client, world):
    async with async_session_factory() as db, db.begin():
        from app.containers.models import Heartbeat
        machine = (await db.get(Heartbeat, NAME)).machine_id
        db.add(RunnerHeartbeat(runner="runner-1", machine_id=machine))
    headers = post_headers(world)
    saved = await client.put(f"/containers/{NAME}/cadangan", data=standby_form(), headers=headers)
    assert saved.status_code == 422 and "satu server" in saved.text
    now = await client.post(f"/containers/{NAME}/cadangan/restore", data={"runner": "runner-1"}, headers=headers)
    assert now.status_code == 409


async def test_restore_now_creates_manual_job_and_runner_switch_on_success(client, world):
    runners.add("runner-2")
    headers = post_headers(world)
    await client.put(f"/containers/{NAME}/cadangan", data=standby_form(), headers=headers)
    resp = await client.post(f"/containers/{NAME}/cadangan/restore", data={"runner": "runner-2"}, headers=headers)
    assert resp.status_code == 200
    again = await client.post(f"/containers/{NAME}/cadangan/restore", data={"runner": "runner-2"}, headers=headers)
    assert again.status_code == 409          # satu restore pada satu waktu
    async with async_session_factory() as db:
        job = (await db.scalars(select(Job).where(Job.kind == "standby_full"))).one()
    assert (job.runner, job.requested_by, job.status) == ("runner-2", "alice", "Menunggu")
    unknown = await client.post(f"/containers/{NAME}/cadangan/restore", data={"runner": "hantu"}, headers=headers)
    assert unknown.status_code == 422
