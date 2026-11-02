import json
from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from app.alerts import service as alert_service
from app.auth.dependencies import CurrentUserDep, require_csrf, require_ready, require_role
from app.backup_control import service as control
from app.backups import service as backup_service
from app.containers import service as container_service
from app.core.config import settings
from app.core.database import DbSession
from app.core.templating import render, templates
from app.dashboard import service as view
from app.dashboard.schemas import BackupRequestForm, ContainerForm
from app.installs import service as install_service
from app.agents.agents_file import AgentsFileWriteError, NameRejected, project_name
from app.agents.dependencies import registry

# Semua akun LumbungDB punya hak yang sama, yaitu admin.
router = APIRouter(
    tags=["dashboard"], dependencies=[Depends(require_csrf), Depends(require_ready), Depends(require_role("admin"))],
)


async def _base(db, user, active: str) -> dict:
    states, nas, alerts, since, now = await alert_service.current_alerts(db)
    return {
        "user": user, "active": active, "states": states, "nas": nas, "alerts": alerts, "alert_since": since,
        "now": now, "views": view.build_views(states, alerts), "writable": nas.writable,
    }


def _done(message: str, *, modal: bool = True) -> Response:
    """Aksi berhasil tanpa isi baru: dialog ditutup, pesan tampil, dan data disegarkan. Kunci event kebab-case."""
    events: dict = {}
    if modal:
        events["close-modal"] = True
    events["show-toast"] = message
    events["live-refresh"] = True
    return Response(status_code=200, headers={"HX-Trigger": json.dumps(events), "HX-Reswap": "none"})


def _find(base: dict, name: str):
    found = next((v for v in base["views"] if v.name == name), None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Container {name} tidak ada.")
    return found


def _control_error(err: control.ControlError) -> HTTPException:
    return HTTPException(status_code=err.status_code, detail=err.detail)


# ---------- dashboard ----------
@router.get("/dashboard")
async def dashboard(request: Request, user: CurrentUserDep, db: DbSession, hari: int = 7):
    base = await _base(db, user, "dashboard")
    days = hari if hari in (7, 14, 30) else 7
    day_list, rows = await view.timeline(db, base["views"], days, base["now"])
    active_views = [v for v in base["views"] if v.status != "off"]
    context = {
        **base, "days": days, "day_list": day_list, "timeline": rows,
        "problem_count": sum(1 for v in active_views if v.level in ("bad", "warn") or v.status == "wait-backup"),
        "active_count": len(active_views), "long_date": view.long_date(base["now"]),
        "day_labels": [view.day_label(day) if days == 7 else str(day.day) for day in day_list],
        "range_label": f"{view.day_label(day_list[0])} – {view.day_label(day_list[-1])} {day_list[-1].year}",
        "server_ip": None,
    }
    response = render(request, "dashboard/dashboard.html", "dashboard/_dashboard.html", context)
    if request.headers.get("hx-trigger-name") == "hari":   # pilihan rentang ikut alamat, supaya bertahan saat dimuat ulang
        response.headers["HX-Push-Url"] = f"/dashboard?hari={days}"
    return response


@router.get("/alerts")
async def alerts_page(request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "alerts")
    return render(request, "dashboard/alerts.html", "dashboard/_alerts.html", base)


@router.get("/settings")
async def settings_page(request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "settings")
    return render(request, "dashboard/settings.html", "dashboard/_settings.html",
                  {**base, "rows": view.settings_rows(), "probe_timeout": settings.MOUNT_PROBE_TIMEOUT_SECONDS})


@router.get("/api/status")
async def api_status(user: CurrentUserDep, db: DbSession):
    states, nas, alerts, _since, _now = await alert_service.current_alerts(db)
    return {
        "nas": {"state": nas.state, "free_percent": nas.free_percent},
        "containers": [
            {
                "name": state.name, "active": state.active, "server": state.server,
                "heartbeat_at": state.heartbeat_at, "last_full": state.last_full,
                "last_binlog": state.last_binlog, "dumps": state.dumps, "files": state.counts,
            } for state in states
        ],
        "alerts": [{"key": a.key, "kind": a.kind, "level": a.level, "cond": a.cond, "container": a.container}
                   for a in alerts],
    }


# ---------- container: daftar dan tambah ----------
@router.get("/containers")
async def containers_page(request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "containers")
    return render(request, "dashboard/containers.html", "dashboard/_containers.html",
                  {**base, "groups": (groups := view.group_by_server(base["views"])),
                   "server_count": sum(1 for g in groups if g.title != view.NOT_CONNECTED)})


@router.get("/containers/new")
async def new_container_page(request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "containers")
    return templates.TemplateResponse(request=request, name="dashboard/wizard.html",
                                      context={**base, "name": "", "port": install_service.DEFAULT_PORT, "error": None, "step": 1})


@router.post("/containers/name-check")
async def name_check(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    error = registry.name_error(name) if name else None
    return templates.TemplateResponse(request=request, name="dashboard/_name_check.html",
                                      context={"name": name, "error": error})


def _base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', '')}"


@router.post("/containers")
async def create_container(request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    name = str(form.get("name", "")).strip()[:100]
    port_text = str(form.get("port", "")).strip()[:10] or str(install_service.DEFAULT_PORT)
    port = install_service.DEFAULT_PORT
    try:
        parsed = ContainerForm.model_validate({"name": name, "port": port_text})
        name, port = parsed.name, parsed.port
        error = registry.name_error(name)
    except ValidationError as err:
        error = ("Port harus angka 1 sampai 65535." if any(e["loc"] == ("port",) for e in err.errors())
                 else "Isi nama container, maksimal 64 karakter.")
    if error is None:
        try:
            token, install_token, expires_at = await install_service.register(db, name, port, user.username)
        except NameRejected as err:
            error = str(err)
        except AgentsFileWriteError:
            # Token belum dibuat dan daftar agen tidak berubah
            error = "Tidak bisa menulis config/agents.conf. Container belum didaftarkan; perbaiki izin file."
    if error:
        return templates.TemplateResponse(
            request=request, name="dashboard/_wizard_name.html",
            context={"name": name, "port": port_text, "error": error, "step": 1},
            status_code=422, headers={"HX-Retarget": "#wizard", "HX-Reswap": "innerHTML"},
        )
    url = _base_url(request)
    context = {
        "name": name, "token": token, "env": install_service.env_text(name, token, url, port), "step": 2,
        "install_command": install_service.install_command(url, install_token), "expires_at": expires_at, "base_url": url,
        "command": f"docker compose -p {project_name(name)} -f docker-compose.lumbung.yml --env-file .env up -d",
    }
    return templates.TemplateResponse(request=request, name="dashboard/_wizard_token.html", context=context)


@router.post("/containers/{name}/install-command")
async def renew_install_command(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    """Dipakai di halaman verifikasi bila perintah hilang atau kedaluwarsa. Token agen tidak ditampilkan di sini."""
    try:
        install_token, expires_at = await install_service.renew_command(db, name, user.username)
    except install_service.InstallError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    url = _base_url(request)
    context = {
        "name": name, "step": 2, "expires_at": expires_at, "base_url": url,
        "install_command": install_service.install_command(url, install_token),
    }
    return templates.TemplateResponse(request=request, name="dashboard/_wizard_renewed.html", context=context)


@router.delete("/containers/{name}/pending")
async def cancel_pending(name: str, db: DbSession):
    try:
        found = await container_service.cancel_pending(db, name)
    except container_service.AlreadyConnected as err:
        raise HTTPException(status_code=409, detail="Agen sudah pernah mengirim heartbeat.") from err
    except AgentsFileWriteError as err:
        raise HTTPException(status_code=500) from err
    if not found:
        raise HTTPException(status_code=404)
    return Response(status_code=200, headers={"HX-Redirect": "/containers"})


@router.get("/containers/{name}/verify")
async def verify_page(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "containers")
    found = _find(base, name)
    install = await install_service.get(db, name)
    return render(request, "dashboard/verify.html", "dashboard/_verify.html",
                  {**base, "view": found, "step": 3, "install": install})


# ---------- detail container ----------
async def _detail_context(db, user, name: str, tab: str) -> dict:
    base = await _base(db, user, "containers")
    found = _find(base, name)
    return {
        **base, "view": found, "tab": tab,
        "disabled_at": await view.disabled_at(db, name) if found.status == "off" else None,
        "schedule_rows": view.schedule_rows(found.state, base["now"]),
        "oldest_dump": view.oldest_dump(found.state), "disk_used": view.disk_used_percent(found.state),
    }


@router.get("/containers/{name}")
async def container_summary(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    context = await _detail_context(db, user, name, "ringkasan")
    return render(request, "dashboard/container.html", "dashboard/_container_summary.html", context)


@router.get("/containers/{name}/backup")
async def container_backup(
    name: str, request: Request, user: CurrentUserDep, db: DbSession,
    kind: str = "", q: str = "", day: str = "", before: int | None = None,
):
    context = await _detail_context(db, user, name, "backup")
    day_range = None
    if day:
        try:
            parsed = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError as err:
            raise HTTPException(status_code=422) from err
        start = datetime.combine(parsed, time.min, view.DISPLAY_TZ).astimezone(UTC).replace(tzinfo=None)
        day_range = (start, start + timedelta(days=1))
    kind_filter = kind if kind in ("fullbackup", "databases", "binlog") else None
    files, next_cursor = await backup_service.list_files(
        db, name, kind=kind_filter, query=q[:100] or None, day_range=day_range, before_id=before,
    )
    context.update(files=files, next_cursor=next_cursor, kind=kind, q=q, day=day)
    # Muat lagi dan filter hanya mengganti baris; penyegaran menukar seluruh #live.
    if before is not None or request.headers.get("hx-target") == "file-rows":
        return templates.TemplateResponse(
            request=request, name="dashboard/_file_rows.html", context=context, headers={"Vary": "HX-Request"},
        )
    context.update(
        requests=await control.requests_for(db, name),
        control_log=await control.control_log_for(db, name),
        integrity=await backup_service.integrity_for(db, name),
        dump_rows=view.dump_rows(context["view"].state, context["now"]),
        request_pill=view.REQUEST_PILL,
    )
    return render(request, "dashboard/container.html", "dashboard/_container_backup.html", context)


@router.get("/containers/{name}/disable")
async def disable_dialog(name: str, request: Request):
    return templates.TemplateResponse(request=request, name="dashboard/_disable_dialog.html", context={"name": name})


@router.post("/containers/{name}/disable")
async def disable_container(name: str, user: CurrentUserDep, db: DbSession):
    try:
        await control.disable_container(db, name, user.username)
    except AgentsFileWriteError as err:
        raise HTTPException(status_code=500) from err
    except control.ControlError as err:
        raise _control_error(err) from err
    return _done(f"{name} dinonaktifkan. File di NAS tidak dihapus.")


# ---------- jadwal ----------
async def _schedule_form(request, db, user, name: str, form, *, status_code=200, headers=None):
    base = await _base(db, user, "containers")
    found = _find(base, name)
    rows, values = view.form_rows(found.state, base["states"], form, base["now"])
    context = {"name": name, "rows": rows, "day_names": view.DAY_NAMES, "valid": not control.schedule_problems(values)}

    return templates.TemplateResponse(request=request, name="dashboard/_schedule_form.html", context=context,
                                      status_code=status_code, headers=headers)


@router.get("/containers/{name}/schedule/edit")
async def schedule_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _schedule_form(request, db, user, name, None)


@router.post("/containers/{name}/schedule/preview")
async def schedule_preview(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _schedule_form(request, db, user, name, await request.form())


@router.put("/containers/{name}/schedule")
async def schedule_update(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    values = {kind: view.cron_from_form(kind, form) for kind, _ in view.SCHEDULE_KINDS}
    if control.schedule_problems(values):
        return await _schedule_form(
            request, db, user, name, form, status_code=422,
            headers={"HX-Retarget": "#schedule-form", "HX-Reswap": "outerHTML"},
        )
    try:
        changed = await control.update_schedule(db, name, values, user.username)
    except control.ControlError as err:
        raise _control_error(err) from err
    return _done(f"Jadwal disimpan, menunggu agen {name}." if changed else "Tidak ada perubahan jadwal.")


# ---------- permintaan backup ----------
@router.get("/containers/{name}/requests/new")
async def request_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "containers")
    found = _find(base, name)
    size, minutes = await control.last_full_duration(db, name)
    databases = sorted(d for d in found.state.dumps if d != "_users")
    return templates.TemplateResponse(
        request=request, name="dashboard/_request_dialog.html",
        context={"name": name, "databases": databases, "last_size": size, "last_minutes": minutes,
                 "busy": await control.active_kinds(db, name)},
    )


@router.post("/containers/{name}/requests")
async def create_request(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    try:
        what = BackupRequestForm.model_validate({"what": form.get("what", "")}).what
    except ValidationError as err:
        raise HTTPException(status_code=422, detail="Jenis backup tidak dikenal.") from err
    kind, _, database = what.partition(":")
    try:
        created = await control.create_request(db, name, kind, database or None, user.username)
    except control.ControlError as err:
        raise _control_error(err) from err
    title = control.request_label(created.kind, created.database)
    if created.status == "Ditolak":
        return _done(f"{title} ditolak: {created.note}")
    return _done(f"{title} diminta, menunggu agen {name}.")


@router.delete("/containers/{name}/requests/{request_id}")
async def cancel_request(name: str, request_id: int, user: CurrentUserDep, db: DbSession):
    try:
        found = await control.cancel_request(db, name, request_id, user.username)
    except control.ControlError as err:
        raise _control_error(err) from err
    if not found:
        raise HTTPException(status_code=404)
    return _done("Permintaan dibatalkan.", modal=False)


# ---------- tinjauan checksum ----------
@router.post("/integrity/{event_id}/review")
async def review_integrity(event_id: int, user: CurrentUserDep, db: DbSession):
    if await backup_service.review_integrity(db, event_id, user.username) is None:
        raise HTTPException(status_code=404)
    return _done("Perbedaan checksum ditandai sudah ditinjau.", modal=False)

