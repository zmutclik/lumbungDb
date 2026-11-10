"""Halaman, sesi, dan aksi tulis dashboard."""
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.auth import service as auth_service
from app.auth.models import User, UserLog
from app.core.config import settings
from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.agents.dependencies import registry
from tests.conftest import AGE, login, make_user
from tests.test_control import beat


async def nas_ok():
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))


@pytest.fixture
async def csrf(client):
    await make_user()
    await nas_ok()
    return await login(client)


def post_headers(csrf):
    return {"X-CSRF-Token": csrf, "HX-Request": "true"}


async def test_pages_redirect_without_session(client):
    for path in ("/dashboard", "/containers", "/alerts", "/settings", "/containers/new", "/account/users"):
        resp = await client.get(path)
        assert resp.status_code == 303 and resp.headers["location"] == "/login", path


async def test_non_admin_role_refused(client):
    await make_user(role="user")
    await login(client)
    for path in ("/dashboard", "/containers", "/account/users", "/api/status"):
        assert (await client.get(path)).status_code == 403, path


async def test_api_status_401_without_session(client):
    assert (await client.get("/api/status")).status_code == 401


async def test_pages_render_with_session(client, csrf):
    for path in ("/dashboard", "/containers", "/alerts", "/settings", "/containers/new", "/account/users"):
        resp = await client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.text[:400]}"
        assert 'style="' not in resp.text, path     # CSP style-src 'self'
        assert "<script>" not in resp.text, path
    assert (await client.get("/api/status")).json()["alerts"] == []


async def test_live_fragment_for_htmx(client, csrf):
    resp = await client.get("/dashboard", headers={"HX-Request": "true"})
    assert 'id="live"' in resp.text and "<html" not in resp.text
    assert resp.headers["vary"] == "HX-Request"
    assert 'hx-swap-oob="true"' in resp.text
    full = await client.get("/dashboard")
    assert full.text.count('id="nav-alerts"') == 1


async def test_write_without_csrf_or_other_origin_403(client, csrf):
    assert (await client.post("/containers", data={"name": "a"})).status_code == 403
    resp = await client.post("/containers", data={"name": "a"},
                             headers={"X-CSRF-Token": csrf, "Origin": "http://jahat.example"})
    assert resp.status_code == 403
    assert registry.entries().active == {}


async def test_add_container_shows_token_once(client, csrf):
    resp = await client.post("/containers", data={"name": "radiologi-db"}, headers=post_headers(csrf))
    assert resp.status_code == 200, resp.text
    token = registry.entries().active["radiologi-db"]
    assert token in resp.text
    assert "LUMBUNG_URL=http://test" in resp.text
    assert "docker compose -p radiologi-db" in resp.text
    for path in ("/containers", "/containers/radiologi-db", "/containers/radiologi-db/backup",
                 "/containers/radiologi-db/verify", "/dashboard", "/alerts"):
        page = await client.get(path)
        assert page.status_code == 200, path
        assert token not in page.text, path
    assert "radiologi-db" in (await client.get("/containers")).text


async def test_add_container_invalid_name_and_collision(client, csrf):
    bad = await client.post("/containers", data={"name": ".titik"}, headers=post_headers(csrf))
    assert bad.status_code == 422 and bad.headers["hx-retarget"] == "#wizard"
    await client.post("/containers", data={"name": "rad.db"}, headers=post_headers(csrf))
    twin = await client.post("/containers", data={"name": "rad-db"}, headers=post_headers(csrf))
    assert twin.status_code == 422 and "project compose" in twin.text
    assert list(registry.entries().active) == ["rad.db"]


async def test_add_container_write_failure_creates_no_token(client, csrf, monkeypatch):
    from app.agents import agents_file

    def refuse(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(agents_file.tempfile, "mkstemp", refuse)
    resp = await client.post("/containers", data={"name": "baru"}, headers=post_headers(csrf))
    assert resp.status_code == 422 and "agents.conf" in resp.text
    assert registry.entries().active == {}


async def test_cancel_pending_only_before_heartbeat(client, csrf):
    await client.post("/containers", data={"name": "a-db"}, headers=post_headers(csrf))
    token = registry.entries().active["a-db"]
    await client.post("/containers", data={"name": "b-db"}, headers=post_headers(csrf))
    await beat(client, {"X-Token": registry.entries().active["b-db"]})
    assert (await client.delete("/containers/b-db/pending", headers=post_headers(csrf))).status_code == 409
    resp = await client.delete("/containers/a-db/pending", headers=post_headers(csrf))
    assert resp.status_code == 200 and resp.headers["hx-redirect"] == "/containers"
    assert registry.authenticate(token) is None
    assert (await client.get("/containers/a-db")).status_code == 404


async def test_disable_keeps_history_and_blocks_agent(client, csrf):
    await client.post("/containers", data={"name": "lama-db"}, headers=post_headers(csrf))
    token = registry.entries().active["lama-db"]
    agent = {"X-Token": token}
    await beat(client, agent)
    resp = await client.post("/containers/lama-db/disable", headers=post_headers(csrf))
    assert resp.status_code == 200
    assert "show-toast" in json.loads(resp.headers["hx-trigger"])
    assert (await client.post("/api/heartbeat", json={}, headers=agent)).status_code == 401
    page = await client.get("/containers/lama-db")
    assert "Nonaktif" in page.text
    assert "lama-db" not in json.dumps((await client.get("/api/status")).json()["alerts"])


async def test_disable_refused_when_agents_conf_invalid(client, csrf):
    await client.post("/containers", data={"name": "rusak-db"}, headers=post_headers(csrf))
    token = registry.entries().active["rusak-db"]
    agent = {"X-Token": token}
    await beat(client, agent)
    path = Path(settings.AGENTS_FILE)
    original = path.read_text()
    path.write_text(original + "baris rusak\n")
    try:
        resp = await client.post("/containers/rusak-db/disable", headers=post_headers(csrf))
        assert resp.status_code == 409
        assert "tidak bisa dibaca" in resp.text and "belum dinonaktifkan" in resp.text
        assert "show-toast" not in resp.headers.get("hx-trigger", "")
        assert path.read_text().endswith("baris rusak\n")
    finally:
        path.write_text(original)
    assert (await client.post("/containers/rusak-db/disable", headers=post_headers(csrf))).status_code == 200
    assert (await client.post("/api/heartbeat", json={}, headers=agent)).status_code == 401


async def test_detail_pages_after_activity(client, csrf):
    token = registry.add("siak-db")
    agent = {"X-Token": token}
    await beat(client, agent)
    await client.put("/api/upload/databases/siakdb/siakdb-20261101_030000.sql.gz.age", content=AGE + b"a", headers=agent)
    await client.put("/api/upload/databases/siakdb/siakdb-20261101_030000.sql.gz.age", content=AGE + b"b", headers=agent)
    await client.put("/api/upload/fullbackup/20261101_020000.xbstream.gz.age", content=AGE + b"f", headers=agent)
    await client.post("/containers/siak-db/requests", data={"what": "dump:siakdb"}, headers=post_headers(csrf))
    for path in ("/containers/siak-db", "/containers/siak-db/backup", "/containers/siak-db/backup?kind=databases&q=siak",
                 "/containers/siak-db/backup?day=2026-11-01", "/dashboard?hari=30", "/containers"):
        resp = await client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.text[:300]}"
    page = (await client.get("/containers/siak-db/backup")).text
    assert "Checksum berbeda" in page and "Tandai sudah ditinjau" in page
    assert "Dump siakdb" in page and "20261101_020000.xbstream.gz.age" in page


async def test_integrity_review_clears_alert(client, csrf):
    agent = {"X-Token": registry.add("siak-db")}
    url = "/api/upload/fullbackup/20261101_020000.xbstream.gz.age"
    await client.put(url, content=AGE + b"a", headers=agent)
    await client.put(url, content=AGE + b"b", headers=agent)
    status = (await client.get("/api/status")).json()
    assert any(alert["key"].startswith("integrity:") for alert in status["alerts"])
    key = next(a["key"] for a in status["alerts"] if a["key"].startswith("integrity:"))
    resp = await client.post(f"/integrity/{key.split(':')[1]}/review", headers=post_headers(csrf))
    assert resp.status_code == 200
    assert not any(a["key"] == key for a in (await client.get("/api/status")).json()["alerts"])


async def test_schedule_dialog_preview_and_save(client, csrf):
    agent = {"X-Token": registry.add("siak-db")}
    await beat(client, agent)
    assert (await client.get("/containers/siak-db/schedule/edit", headers={"HX-Request": "true"})).status_code == 200
    form = {"full_mode": "custom", "full_cron": "bukan cron", "dump_mode": "day", "dump_time": "03:30",
            "binlog_mode": "mins", "binlog_every": "15"}
    preview = await client.post("/containers/siak-db/schedule/preview", data=form, headers=post_headers(csrf))
    assert "Bukan cron 5 kolom" in preview.text and "disabled" in preview.text
    invalid = await client.put("/containers/siak-db/schedule", data=form, headers=post_headers(csrf))
    assert invalid.status_code == 422 and invalid.headers["hx-retarget"] == "#schedule-form"
    form.update(full_mode="off")
    preview = await client.post("/containers/siak-db/schedule/preview", data=form, headers=post_headers(csrf))
    assert "Full mati" in preview.text
    form.update(full_mode="week", full_time="02:15", full_dow="1")
    saved = await client.put("/containers/siak-db/schedule", data=form, headers=post_headers(csrf))
    assert saved.status_code == 200
    reply = await beat(client, agent)
    assert reply["schedule"]["full"] == "15 2 * * 1" and reply["schedule"]["dump"] == "30 3 * * *"
    assert "Menunggu diterapkan" in (await client.get("/containers/siak-db/backup")).text


async def test_session_expires_without_extension(client, csrf):
    assert settings.SESSION_TTL == settings.SESSION_MAX_AGE
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE user_sessions SET expires_at = NOW() - INTERVAL 1 SECOND"))
    assert (await client.get("/dashboard")).status_code == 303


async def test_cookie_is_strict(client):
    await make_user()
    page = await client.get("/login")
    resp = await client.post("/login", data={"username": "alice", "password": "s3cret-pass-12",
                                             "csrf_token": page.cookies["login_csrf"]})
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


async def test_failed_logins_lock_and_are_logged(client):
    await make_user()
    for _ in range(settings.LOGIN_MAX_FAILURES):
        page = await client.get("/login")
        resp = await client.post("/login", data={"username": "alice", "password": "salah-sekali-12",
                                                 "csrf_token": page.cookies["login_csrf"]})
        assert resp.status_code == 400
    page = await client.get("/login")
    resp = await client.post("/login", data={"username": "alice", "password": "s3cret-pass-12",
                                             "csrf_token": page.cookies["login_csrf"]})
    assert resp.status_code == 429
    async with async_session_factory() as db:
        actions = list(await db.scalars(select(UserLog.action)))
    assert "login_gagal" in actions and "login_ditahan" in actions


async def test_new_account_must_change_password(client, csrf):
    resp = await client.post("/account/users", data={"username": "budi"}, headers=post_headers(csrf))
    assert resp.status_code == 200
    async with async_session_factory() as db:
        row = await db.scalar(select(User).where(User.username == "budi"))
    assert row.must_change_password and row.role == "admin"
    start = resp.text.index('x-ref="source">') + len('x-ref="source">')
    password = resp.text[start:resp.text.index("</code>", start)]
    assert len(password) >= 12

    client.cookies.clear()
    csrf_budi = await login(client, "budi", password)
    blocked = await client.get("/dashboard")
    assert blocked.status_code == 303 and blocked.headers["location"] == "/account/password"
    assert (await client.get("/api/status")).status_code == 403
    assert (await client.get("/account/password")).status_code == 200

    weak = await client.post("/account/password", data={
        "current_password": password, "new_password": "pendek", "repeat_password": "pendek",
        "csrf_token": csrf_budi})
    assert weak.status_code == 422
    done = await client.post("/account/password", data={
        "current_password": password, "new_password": "password-baru-budi", "repeat_password": "password-baru-budi",
        "csrf_token": csrf_budi})
    assert done.status_code == 303
    client.cookies.clear()
    await login(client, "budi", "password-baru-budi")
    assert (await client.get("/dashboard")).status_code == 200


async def test_disabled_account_loses_session_immediately(client, csrf):
    async with async_session_factory() as db:
        await auth_service.create_user(db, "budi", "password-budi-123")
    async with async_session_factory() as db:
        await auth_service.login(db, "budi", "password-budi-123", "10.0.0.5")
    resp = await client.post("/account/users/budi/disable", headers=post_headers(csrf))
    assert resp.status_code == 200
    async with async_session_factory() as db:
        assert await auth_service.login(db, "budi", "password-budi-123", "10.0.0.6") is None
        count = await db.scalar(text("SELECT COUNT(*) FROM user_sessions s JOIN users u ON u.id=s.user_id "
                                     "WHERE u.username='budi'"))
    assert count == 0


async def test_cannot_disable_self(client, csrf):
    resp = await client.post("/account/users/alice/disable", headers=post_headers(csrf))
    assert resp.status_code == 409


async def test_reset_password_ends_sessions_and_forces_change(client, csrf):
    async with async_session_factory() as db:
        await auth_service.create_user(db, "budi", "password-budi-123")
    resp = await client.post("/account/users/budi/reset-password", headers=post_headers(csrf))
    assert resp.status_code == 200 and "Password direset" in resp.text
    async with async_session_factory() as db:
        row = await db.scalar(select(User).where(User.username == "budi"))
    assert row.must_change_password is True


async def test_settings_never_show_secrets(client, csrf, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_TOKEN", "rahasia-telegram")
    page = await client.get("/settings")
    assert "rahasia-telegram" not in page.text and "terisi" in page.text


async def test_nas_banner_on_every_page(client, csrf):
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("hang"))
    for path in ("/dashboard", "/containers", "/alerts", "/settings"):
        assert "NAS tidak merespons" in (await client.get(path)).text, path


async def test_watchdog_public_and_reports_stale_checker(client):
    assert (await client.get("/api/watchdog")).status_code == 503
    await nas_ok()
    assert (await client.get("/api/watchdog")).json() == {"nas": "ok"}




async def test_filter_returns_only_rows(client, csrf):
    agent = {"X-Token": registry.add("siak-db")}
    await client.put("/api/upload/fullbackup/a.gz.age", content=AGE + b"a", headers=agent)
    resp = await client.get("/containers/siak-db/backup?kind=binlog",
                            headers={"HX-Request": "true", "HX-Target": "file-rows"})
    assert "<html" not in resp.text and 'id="live"' not in resp.text and "a.gz.age" not in resp.text


async def test_status_pill_uses_short_label(client, csrf):
    agent = {"X-Token": registry.add("siak-db")}
    await beat(client, agent)
    page = await client.get("/containers")
    assert "Belum ada backup" in page.text
    await client.put("/api/upload/fullbackup/a.gz.age", content=AGE + b"a", headers=agent)
    await client.put("/api/upload/fullbackup/a.gz.age", content=AGE + b"b", headers=agent)
    page = await client.get("/containers")
    assert "Checksum berbeda" in page.text and "siak-db: " not in page.text


async def test_heartbeat_ip_shown(client, csrf):
    await beat(client, {"X-Token": registry.add("siak-db")})
    assert "127.0.0.1" in (await client.get("/containers/siak-db")).text


# ---------- perintah pasang ----------
def _install_token(html: str) -> str:
    return re.search(r"INSTALL_TOKEN=(lmi_[\w-]+)", html).group(1)


async def _claim(client, token, machine="a" * 32):
    return await client.post("/api/install/session", headers={"X-Install-Token": token, "X-Machine-Id": machine})


async def test_install_command_flow(client, csrf):
    resp = await client.post("/containers", data={"name": "radiologi-db", "port": "3307"}, headers=post_headers(csrf))
    assert resp.status_code == 200, resp.text
    agent_token = registry.entries().active["radiologi-db"]
    install_token = _install_token(resp.text)
    assert "LUMBUNG_URL=http://test INSTALL_TOKEN=" in resp.text
    assert install_token != agent_token

    # Tanpa login dan tanpa CSRF: yang melindungi adalah token. Isi .env tidak berisi user dan password.
    claimed = await _claim(client, install_token)
    assert claimed.status_code == 200, claimed.text
    assert claimed.headers["content-type"].startswith("text/plain")
    for line in (f"LUMBUNG_TOKEN={agent_token}", "COMPOSE_PROJECT_NAME=radiologi-db", "DATA_PORT=3307",
                 "DB_NAME=radiologi", "DB_ROOTPASSWORD=", "DB_APPUSER=", "DB_APPPASS=", "BACKUP_USER=", "BACKUP_PASS="):
        assert line in claimed.text.splitlines(), line

    assert (await _claim(client, install_token)).status_code == 200   # jalankan ulang di mesin yang sama
    assert (await _claim(client, install_token, "b" * 32)).status_code == 403
    assert (await _claim(client, "lmi_salah")).status_code == 401
    assert (await _claim(client, install_token, "bukan machine id")).status_code == 422

    page = await client.get("/containers/radiologi-db/verify")
    assert "Perintah pasang dijalankan" in page.text and install_token not in page.text


async def test_install_command_expired_rejected(client, csrf):
    resp = await client.post("/containers", data={"name": "a-db"}, headers=post_headers(csrf))
    async with async_session_factory() as db, db.begin():
        await db.execute(text("UPDATE container_installs SET expires_at = NOW() - INTERVAL 1 SECOND"))
    expired = await _claim(client, _install_token(resp.text))
    assert expired.status_code == 410 and "kedaluwarsa" in expired.text


async def test_install_command_renew_replaces_old_and_unbinds_machine(client, csrf):
    resp = await client.post("/containers", data={"name": "a-db", "port": "3310"}, headers=post_headers(csrf))
    old = _install_token(resp.text)
    assert (await _claim(client, old)).status_code == 200
    renewed = await client.post("/containers/a-db/install-command", headers=post_headers(csrf))
    assert renewed.status_code == 200, renewed.text
    assert registry.entries().active["a-db"] not in renewed.text   # token agen hanya tampil sekali
    new = _install_token(renewed.text)
    assert (await _claim(client, old)).status_code == 401
    claimed = await _claim(client, new, "b" * 32)
    assert claimed.status_code == 200 and "DATA_PORT=3310" in claimed.text


async def test_install_command_dies_after_heartbeat_and_cancel(client, csrf):
    resp = await client.post("/containers", data={"name": "a-db"}, headers=post_headers(csrf))
    token = _install_token(resp.text)
    agent_token = registry.entries().active["a-db"]
    assert (await client.post("/api/heartbeat", json={"server": "srv"}, headers={"X-Token": agent_token})).status_code == 200
    assert (await _claim(client, token)).status_code == 410
    assert (await client.post("/containers/a-db/install-command", headers=post_headers(csrf))).status_code == 409

    resp = await client.post("/containers", data={"name": "b-db"}, headers=post_headers(csrf))
    token = _install_token(resp.text)
    assert (await client.delete("/containers/b-db/pending", headers=post_headers(csrf))).status_code == 200
    assert (await _claim(client, token)).status_code == 401


async def test_add_container_rejects_bad_port(client, csrf):
    resp = await client.post("/containers", data={"name": "a-db", "port": "70000"}, headers=post_headers(csrf))
    assert resp.status_code == 422 and "Port harus" in resp.text
    assert registry.entries().active == {}


async def test_install_files_are_public_and_compose_matches_image(client):
    script = await client.get("/install.sh")
    assert script.status_code == 200 and script.text.startswith("#!/bin/sh")
    compose = await client.get("/install/docker-compose.lumbung.yml")
    reference = Path(__file__).resolve().parents[2] / "mariadb" / "docker-compose.lumbung.yml"
    assert compose.status_code == 200 and compose.text == reference.read_text()
