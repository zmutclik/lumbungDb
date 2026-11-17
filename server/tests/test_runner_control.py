"""Container dikelola runner: Tambah container langsung di mesin runner, start/stop/restart, dan CPU/memori runner."""
import pytest
from sqlalchemy import select

from app.agents.dependencies import registry as agents
from app.containers.models import Container
from app.core.config import settings
from app.core.database import async_session_factory
from app.runners import service
from app.runners.dependencies import registry as runners
from app.runners.models import Job, ManagedContainer, RunnerHeartbeat
from tests.conftest import login, make_user
from tests.test_dashboard import nas_ok, post_headers
from tests.test_runners import clean_files  # noqa: F401

NAME = "radiologi-db"


@pytest.fixture
async def csrf(client):
    await make_user()
    await nas_ok()
    return await login(client)


@pytest.fixture
def runner_headers():
    return {"X-Runner-Token": runners.add("runner-1")}


async def add_on_runner(client, csrf, runner="runner-1", name=NAME, port="3310"):
    return await client.post("/containers", data={"name": name, "port": port, "runner": runner},
                             headers=post_headers(csrf))


async def claim(client, headers, lane="control"):
    resp = await client.post(f"/api/runner/jobs/claim?lane={lane}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["job"]


async def test_wizard_offers_runners(client, csrf, runner_headers):
    page = await client.get("/containers/new")
    assert "Mesin runner runner-1" in page.text


async def test_add_on_runner_creates_install_job_without_curl_command(client, csrf, runner_headers):
    resp = await add_on_runner(client, csrf)
    assert resp.status_code == 200, resp.text
    assert resp.headers["HX-Redirect"] == f"/containers/{NAME}/verify"
    token = agents.entries().active[NAME]
    async with async_session_factory() as db:
        row = await db.get(ManagedContainer, NAME)
        job = (await db.scalars(select(Job).where(Job.container == NAME))).one()
    assert (row.runner, row.port, row.origin) == ("runner-1", 3310, "install")
    assert (job.kind, job.status, job.requested_by) == ("container_install", "Menunggu", "alice")
    verify = await client.get(f"/containers/{NAME}/verify")
    assert "Menunggu runner runner-1" in verify.text and token not in verify.text
    assert "curl" not in verify.text


async def test_unknown_runner_is_refused_without_registering(client, csrf, runner_headers):
    resp = await add_on_runner(client, csrf, runner="tidak-ada")
    assert resp.status_code == 422 and "Runner tidak dikenal" in resp.text
    assert NAME not in agents.entries().active


async def test_install_only_on_control_lane_with_context(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    assert await claim(client, runner_headers, "main") is None          # runner lama tidak pernah menerimanya
    job = await claim(client, runner_headers)
    info = job["install"]
    assert (job["kind"], job["container"], job["files"]) == ("container_install", NAME, [])
    assert (info["name"], info["project"], info["port"], info["db_name"]) == (NAME, NAME, 3310, "radiologi")
    assert info["token"] == agents.entries().active[NAME] and info["image"].startswith("zmutclik/mariadb:lumbungdb-")
    resp = await client.post(f"/api/runner/jobs/{job['id']}/result", headers=runner_headers,
                             json={"status": "Lolos", "note": "berjalan", "results": []})
    assert resp.status_code == 200
    verify = await client.get(f"/containers/{NAME}/verify")
    assert "Dipasang oleh runner runner-1" in verify.text


async def test_other_runner_cannot_claim_install(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    other = {"X-Runner-Token": runners.add("runner-2")}
    assert await claim(client, other) is None


async def test_control_lane_not_blocked_by_running_restore_test(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    async with async_session_factory() as db, db.begin():
        db.add(Job(runner="runner-1", container=NAME, kind="restore_test", status=service.STATUS_RUNNING))
    assert (await claim(client, runner_headers))["kind"] == "container_install"


async def test_failed_install_can_be_retried_and_cancel_cleans_up(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    job = await claim(client, runner_headers)
    await client.post(f"/api/runner/jobs/{job['id']}/result", headers=runner_headers,
                      json={"status": "Tidak terlaksana", "note": "jalankan container: port is already allocated"})
    verify = await client.get(f"/containers/{NAME}/verify")
    assert "port is already allocated" in verify.text and "Pasang ulang" in verify.text
    resp = await client.post(f"/containers/{NAME}/install-runner", headers=post_headers(csrf))
    assert resp.status_code == 200
    resp = await client.post(f"/containers/{NAME}/install-runner", headers=post_headers(csrf))
    assert resp.status_code == 409                       # yang baru masih menunggu
    resp = await client.delete(f"/containers/{NAME}/pending", headers=post_headers(csrf))
    assert resp.status_code == 200
    async with async_session_factory() as db:
        assert await db.get(Container, NAME) is None and await db.get(ManagedContainer, NAME) is None
        assert not (await db.scalars(select(Job).where(Job.container == NAME))).all()


async def test_power_actions_go_to_owning_runner(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    install = await claim(client, runner_headers)
    await client.post(f"/api/runner/jobs/{install['id']}/result", headers=runner_headers, json={"status": "Lolos"})
    resp = await client.post(f"/containers/{NAME}/power/restart", headers=post_headers(csrf))
    assert resp.status_code == 200, resp.text
    assert (await client.post(f"/containers/{NAME}/power/stop", headers=post_headers(csrf))).status_code == 409
    assert (await client.post(f"/containers/{NAME}/power/hapus", headers=post_headers(csrf))).status_code == 422
    job = await claim(client, runner_headers)
    assert (job["kind"], job["container"]) == ("container_restart", NAME) and "install" not in job


async def test_power_refused_for_container_not_managed(client, csrf, runner_headers):
    await client.post("/containers", data={"name": "lain-db"}, headers=post_headers(csrf))
    resp = await client.post("/containers/lain-db/power/stop", headers=post_headers(csrf))
    assert resp.status_code == 404


async def test_heartbeat_stores_host_metrics_and_page_shows_them(client, csrf, runner_headers):
    resp = await client.post("/api/runner/heartbeat", headers=runner_headers, json={
        "server": "vm-runner", "version": "r3", "cpu_percent": 42.5, "cpu_count": 8, "load1": 1.5,
        "mem_total": 16 * 1024 ** 3, "mem_available": 4 * 1024 ** 3,
        "containers": [{"name": NAME, "state": "running", "status": "Up 2 hours (healthy)"}]})
    assert resp.status_code == 200, resp.text
    async with async_session_factory() as db:
        row = await db.get(RunnerHeartbeat, "runner-1")
    assert (row.cpu_percent, row.cpu_count, row.mem_total) == (42.5, 8, 16 * 1024 ** 3)
    page = (await client.get("/runners")).text
    assert "42.5%" in page and "dari 8 core" in page and "(75%)" in page and "running" in page


async def test_heartbeat_rejects_impossible_cpu(client, runner_headers):
    resp = await client.post("/api/runner/heartbeat", headers=runner_headers, json={"cpu_percent": 250})
    assert resp.status_code == 422


async def test_container_head_shows_state_and_actions(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    await client.post("/api/runner/heartbeat", headers=runner_headers,
                      json={"containers": [{"name": NAME, "state": "exited", "status": "Exited (0)"}]})
    page = (await client.get(f"/containers/{NAME}")).text
    assert "di mesin runner runner-1, port 3310" in page and "exited" in page
    assert f"/containers/{NAME}/power/start" in page and f"/containers/{NAME}/power/stop" in page



async def installed(client, csrf, runner_headers):
    await add_on_runner(client, csrf)
    job = await claim(client, runner_headers)
    await client.post(f"/api/runner/jobs/{job['id']}/result", headers=runner_headers, json={"status": "Lolos"})
    await client.post("/api/runner/heartbeat", headers=runner_headers, json={"containers": [
        {"name": NAME, "state": "running", "status": "Up", "image": "zmutclik/mariadb:lumbungdb-11.4-a5"}]})


async def test_update_dialog_suggests_same_mariadb_with_latest_agent(client, csrf, runner_headers):
    await installed(client, csrf, runner_headers)
    page = await client.get(f"/containers/{NAME}/image-update")
    assert page.status_code == 200
    latest = settings.IMAGE_TAG_DEFAULT.split("-a")[1]
    assert f'value="11.4-a{latest}"' in page.text and "<code>11.4-a5</code>" in page.text


async def test_update_creates_job_with_target_image(client, csrf, runner_headers):
    await installed(client, csrf, runner_headers)
    resp = await client.post(f"/containers/{NAME}/image-update", data={"tag": "12.3-a7", "confirm": "yes"},
                             headers=post_headers(csrf))
    assert resp.status_code == 200, resp.text
    job = await claim(client, runner_headers)
    assert job["kind"] == "container_update" and job["update"] == {"image": "zmutclik/mariadb:lumbungdb-12.3-a7"}


@pytest.mark.parametrize("tag,message", [("10.11-a7", "tidak boleh turun"), ("11.4-a5", "sudah memakai"),
                                         ("latest", "Tag harus"), ("12.3-a7", "Centang")])
async def test_update_rejects_downgrade_same_bad_tag_or_no_confirm(client, csrf, runner_headers, tag, message):
    await installed(client, csrf, runner_headers)
    data = {"tag": tag} if message == "Centang" else {"tag": tag, "confirm": "yes"}
    resp = await client.post(f"/containers/{NAME}/image-update", data=data, headers=post_headers(csrf))
    assert resp.status_code == 422 and message in resp.text
    assert await claim(client, runner_headers) is None


async def test_update_not_offered_for_unmanaged(client, csrf, runner_headers):
    await client.post("/containers", data={"name": "lain-db"}, headers=post_headers(csrf))
    assert (await client.get("/containers/lain-db/image-update")).status_code == 404
    resp = await client.post("/containers/lain-db/image-update", data={"tag": "12.3-a7", "confirm": "yes"},
                             headers=post_headers(csrf))
    assert resp.status_code == 404


async def test_image_tags_from_setting_sorted_with_default(monkeypatch):
    from app.runners import control
    monkeypatch.setattr(settings, "IMAGE_TAGS", "11.4-a7, salah ,10.11-a7,11.4-a6")
    monkeypatch.setattr(settings, "IMAGE_TAG_DEFAULT", "12.3-a7")
    assert control.image_tags() == ["12.3-a7", "11.4-a7", "11.4-a6", "10.11-a7"]
    assert control.update_choices("11.4-a6") == ["12.3-a7", "11.4-a7"]
    assert control.update_choices(None) == control.image_tags()


async def test_install_uses_chosen_tag_and_update_records_new_tag(client, csrf, runner_headers):
    resp = await client.post("/containers", data={"name": NAME, "port": "3310", "runner": "runner-1",
                                                  "image_tag": "11.4-a7"}, headers=post_headers(csrf))
    assert resp.status_code == 200, resp.text
    job = await claim(client, runner_headers)
    assert job["install"]["image"] == "zmutclik/mariadb:lumbungdb-11.4-a7"
    await client.post(f"/api/runner/jobs/{job['id']}/result", headers=runner_headers, json={"status": "Lolos"})
    dialog = (await client.get(f"/containers/{NAME}/image-update")).text
    assert "<code>11.4-a7</code>" in dialog                          # dari catatan pemasangan, tanpa heartbeat
    assert 'value="12.3-a7"' in dialog and 'value="10.11-a7"' not in dialog and 'value="11.4-a7"' not in dialog
    await client.post(f"/containers/{NAME}/image-update", data={"tag": "12.3-a7", "confirm": "yes"},
                      headers=post_headers(csrf))
    job = await claim(client, runner_headers)
    await client.post(f"/api/runner/jobs/{job['id']}/result", headers=runner_headers, json={"status": "Lolos"})
    async with async_session_factory() as db:
        assert (await db.get(ManagedContainer, NAME)).image_tag == "12.3-a7"


async def test_tag_outside_list_is_refused(client, csrf, runner_headers):
    resp = await client.post("/containers", data={"name": NAME, "runner": "runner-1", "image_tag": "99.9-a1"},
                             headers=post_headers(csrf))
    assert resp.status_code == 422 and "dari daftar" in resp.text and NAME not in agents.entries().active
    await installed(client, csrf, runner_headers)
    resp = await client.post(f"/containers/{NAME}/image-update", data={"tag": "12.4-a7", "confirm": "yes"},
                             headers=post_headers(csrf))
    assert resp.status_code == 422 and "IMAGE_TAGS" in resp.text


async def agent_reports(version):
    from app.containers.models import Heartbeat
    async with async_session_factory() as db, db.begin():
        db.add(Heartbeat(container=NAME, server="vm-runner", machine_id="a" * 32, info={"agent_version": version}))


async def test_agent_card_offers_update_button_for_managed_container(client, csrf, runner_headers, monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_TAG_DEFAULT", "12.3-a7")
    await installed(client, csrf, runner_headers)
    await agent_reports("12.3-a5")
    page = (await client.get(f"/containers/{NAME}")).text
    assert "Update ke 12.3-a7" in page and "image-update?tag=12.3-a7" in page and "Ganti IMAGE_TAG" not in page
    dialog = (await client.get(f"/containers/{NAME}/image-update?tag=12.3-a7")).text
    assert '<option value="12.3-a7" selected>' in dialog
    await client.post(f"/containers/{NAME}/image-update", data={"tag": "12.3-a7", "confirm": "yes"},
                      headers=post_headers(csrf))
    assert "Update image menunggu" in (await client.get(f"/containers/{NAME}")).text


async def test_update_button_tag_missing_from_list_is_explained(client, csrf, runner_headers, monkeypatch):
    await installed(client, csrf, runner_headers)
    await agent_reports("12.3-a5")
    monkeypatch.setattr(settings, "IMAGE_TAGS", "11.4-a7")
    dialog = (await client.get(f"/containers/{NAME}/image-update?tag=12.3-a9")).text
    assert "belum ada di daftar IMAGE_TAGS" in dialog


async def test_manual_container_keeps_manual_instruction(client, csrf, runner_headers, monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_TAG_DEFAULT", "12.3-a7")
    await client.post("/containers", data={"name": NAME}, headers=post_headers(csrf))
    await agent_reports("12.3-a5")
    page = (await client.get(f"/containers/{NAME}")).text
    assert "Ganti IMAGE_TAG" in page and "Update ke" not in page
