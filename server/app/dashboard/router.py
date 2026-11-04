import json
from datetime import UTC, datetime, time, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, literal_column, select

from app.ai import service as ai_service
from app.ai.providers import get_provider
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
from app.manual_restore import service as manual_service
from app.nas import scan as nas_scan
from app.nas import service as nas_service
from app.notifications import service as notification_service
from app.runner_admin import service as runner_view
from app.runners import service as runner_service
from app.runners import promote as promote_service
from app.runners import standby as standby_service
from app.runners.dependencies import registry as runner_registry
from app.containers.models import Heartbeat
from app.runners.models import RunnerHeartbeat
from app.backup_control import cron as cron_lib
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
    base["notifications"] = await notification_service.recent(db, 10)
    return render(request, "dashboard/alerts.html", "dashboard/_alerts.html", base)


@router.get("/settings")
async def settings_page(request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "settings")
    return render(request, "dashboard/settings.html", "dashboard/_settings.html",
                  {**base, "rows": view.settings_rows(), "probe_timeout": settings.MOUNT_PROBE_TIMEOUT_SECONDS,
                   "scans": await nas_scan.recent_scans(db)})


@router.post("/settings/rescan")
async def rescan_nas(user: CurrentUserDep, db: DbSession):
    """Membangun lagi metadata dari file di NAS. Tidak mengubah file; berjalan di worker."""
    async with db.begin():
        nas = await nas_service.get_view(db)
    if nas.problem:
        raise HTTPException(status_code=409, detail="NAS bermasalah; pindai ulang menunggu sampai NAS pulih.")
    scan = await nas_scan.request_scan(db, user.username)
    if scan is None:
        raise HTTPException(status_code=409, detail="Masih ada pindaian yang menunggu atau berjalan.")
    return _done("Pindai ulang diminta; worker akan memulainya sebentar lagi.", modal=False)


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
    context.update(ai_enabled=get_provider() is not None, ai_explanation=await ai_service.latest_explanation(db, name))
    return render(request, "dashboard/container.html", "dashboard/_container_summary.html", context)


@router.post("/containers/{name}/ai/explain")
async def explain_container(name: str, user: CurrentUserDep, db: DbSession):
    """Penjelasan AI atas keadaan container. Hanya membaca dan menyimpan penjelasan; alert tidak berubah."""
    if get_provider() is None:
        raise HTTPException(status_code=409, detail="Lapisan AI belum dinyalakan (AI_PROVIDER).")
    base = await _base(db, user, "containers")
    found = _find(base, name)
    alerts = list(found.alerts)
    await db.rollback()
    async with httpx.AsyncClient() as client:
        await ai_service.explain_container(db, client, name, alerts, user.username)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


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


# ---------- uji restore ----------
@router.get("/containers/{name}/uji-restore")
async def container_tests(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    context = await _detail_context(db, user, name, "uji")
    cfg = await runner_view.container_settings(db, name)
    expr = cfg.test_schedule if cfg else None
    jobs = await runner_view.recent_jobs(db, container=name, limit=10)
    last_job, last_pass = await runner_view.last_outcome(db, name)
    known = await runner_view.known_databases(db, name)
    untested = []
    if cfg and not cfg.test_full:
        untested.append("full")
    if cfg and cfg.test_databases:
        untested += [d for d in known if d not in cfg.test_databases]
    beat = await db.get(RunnerHeartbeat, cfg.test_runner) if cfg and cfg.test_runner else None
    machine = context["view"].state.machine_id
    next_run = None
    if expr and expr != "off":
        local_now = datetime.now(view.DISPLAY_TZ).replace(tzinfo=None)
        due = cron_lib.next_run(expr, local_now)
        next_run = due.replace(tzinfo=view.DISPLAY_TZ).astimezone(UTC).replace(tzinfo=None) if due else None
    context.update(
        settings_row=cfg, schedule_label=cron_lib.label(expr) if expr else "—", next_run=next_run, jobs=jobs,
        results=await runner_view.results_by_job(db, [j.id for j in jobs]), last_job=last_job, last_pass=last_pass,
        untested=untested, kind_label=runner_view.KIND_LABEL,
        banner_same_server=bool(beat and beat.machine_id and machine and beat.machine_id == machine),
    )
    return render(request, "dashboard/container.html", "runners/_container_tests.html", context)


async def _test_form(request, db, name, form, *, status_code=200, error=None, headers=None):
    cfg = await runner_view.container_settings(db, name)
    if form is None:
        values = {**runner_view.schedule_form(cfg.test_schedule if cfg else None),
                  "runner": cfg.test_runner if cfg else "", "full": cfg.test_full if cfg else True,
                  "databases": list(cfg.test_databases or []) if cfg else []}
    else:
        expr = view.cron_from_form("test", form)
        values = {**runner_view.schedule_form(expr), "mode": str(form.get("test_mode", "day")),
                  "time": str(form.get("test_time", "03:30")), "dow": str(form.get("test_dow", "0")),
                  "cron": str(form.get("test_cron", "")), "runner": str(form.get("runner", "")),
                  "full": form.get("full") == "1", "databases": form.getlist("db")}
    return templates.TemplateResponse(
        request=request, name="runners/_test_settings.html", status_code=status_code, headers=headers or {},
        context={"name": name, "values": values, "error": error, "day_names": view.DAY_NAMES,
                 "runner_names": sorted(runner_registry.entries().active),
                 "databases": await runner_view.known_databases(db, name)},
    )


@router.get("/containers/{name}/uji-restore/edit")
async def test_settings_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _test_form(request, db, name, None)


@router.post("/containers/{name}/uji-restore/preview")
async def test_settings_preview(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _test_form(request, db, name, await request.form())


@router.put("/containers/{name}/uji-restore")
async def test_settings_save(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    try:
        await runner_view.save_settings(
            db, name, runner=str(form.get("runner", "")), schedule=view.cron_from_form("test", form),
            full=form.get("full") == "1", databases=form.getlist("db"))
    except runner_view.SettingsError as err:
        if err.status_code == 422:
            return await _test_form(request, db, name, form, status_code=422, error=err.detail,
                                    headers={"HX-Retarget": "#test-settings-form", "HX-Reswap": "outerHTML"})
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _done("Pengaturan uji restore disimpan.")


@router.post("/containers/{name}/uji-restore/run")
async def test_run_now(name: str, user: CurrentUserDep, db: DbSession):
    async with db.begin():      # dibaca lebih dulu; create_restore_test membuka transaksinya sendiri
        cfg = await runner_view.container_settings(db, name)
        chosen = (cfg.test_runner, cfg.test_full, cfg.test_databases) if cfg else None
    if chosen is None or not chosen[0]:
        raise HTTPException(status_code=409, detail="Pilih runner uji lebih dulu.")
    runner, full, databases = chosen
    if runner not in runner_registry.entries().active:
        raise HTTPException(status_code=409, detail="Runner uji nonaktif.")
    try:
        await runner_service.create_restore_test(
            db, name, runner, full=full, databases=databases, requested_by=user.username)
    except runner_service.RunnerError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _done(f"Uji restore diminta, menunggu runner {runner}.", modal=False)


# ---------- database cadangan ----------
STANDBY_FULL_MODES = ("off", "day", "week", "custom")
STANDBY_BINLOG_MODES = ("off", "mins", "hours", "custom")


@router.get("/containers/{name}/cadangan")
async def container_standby(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    context = await _detail_context(db, user, name, "cadangan")
    cfg = await runner_view.container_settings(db, name)
    state = await runner_view.standby_state(db, name)
    full_expr = (cfg.standby_full_schedule if cfg else None) or settings.STANDBY_FULL_DEFAULT
    binlog_expr = (cfg.standby_binlog_schedule if cfg else None) or settings.STANDBY_BINLOG_DEFAULT
    beat = await db.get(RunnerHeartbeat, state.runner) if state and state.runner else None
    context.update(
        cfg=cfg, standby=state, runs=await runner_view.standby_runs(db, name),
        full_label=cron_lib.label(full_expr), binlog_label=cron_lib.label(binlog_expr),
        standby_server=beat.server if beat else None,
        broken=bool(state and state.applied_binlog_no is None and state.full_backup_id is not None),
        runner_names=sorted(runner_registry.entries().active),
    )
    return render(request, "dashboard/container.html", "runners/_container_standby.html", context)


async def _standby_form(request, db, name, form, *, status_code=200, error=None, headers=None):
    cfg = await runner_view.container_settings(db, name)
    if form is None:
        values = {
            "enabled": bool(cfg and cfg.standby_enabled), "runner": (cfg.standby_runner if cfg else "") or "",
            "full": runner_view.schedule_form((cfg.standby_full_schedule if cfg else None) or settings.STANDBY_FULL_DEFAULT,
                                              STANDBY_FULL_MODES),
            "binlog": runner_view.schedule_form(
                (cfg.standby_binlog_schedule if cfg else None) or settings.STANDBY_BINLOG_DEFAULT, STANDBY_BINLOG_MODES),
        }
    else:
        def part(kind, modes):
            parsed = runner_view.schedule_form(view.cron_from_form(kind, form), modes)
            parsed.update(mode=str(form.get(f"{kind}_mode", parsed["mode"])), time=str(form.get(f"{kind}_time", "03:00")),
                          dow=str(form.get(f"{kind}_dow", "0")), cron=str(form.get(f"{kind}_cron", "")),
                          every=str(form.get(f"{kind}_every", parsed["every"])))
            return parsed
        values = {"enabled": form.get("enabled") == "1", "runner": str(form.get("runner", "")),
                  "full": part("sfull", STANDBY_FULL_MODES), "binlog": part("sbinlog", STANDBY_BINLOG_MODES)}
    return templates.TemplateResponse(
        request=request, name="runners/_standby_settings.html", status_code=status_code, headers=headers or {},
        context={"name": name, "values": values, "error": error, "day_names": view.DAY_NAMES,
                 "runner_names": sorted(runner_registry.entries().active)})


@router.get("/containers/{name}/cadangan/edit")
async def standby_settings_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _standby_form(request, db, name, None)


@router.post("/containers/{name}/cadangan/preview")
async def standby_settings_preview(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _standby_form(request, db, name, await request.form())


@router.put("/containers/{name}/cadangan")
async def standby_settings_save(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    try:
        await runner_view.save_standby_settings(
            db, name, enabled=form.get("enabled") == "1", runner=str(form.get("runner", "")),
            full_schedule=view.cron_from_form("sfull", form), binlog_schedule=view.cron_from_form("sbinlog", form))
    except runner_view.SettingsError as err:
        if err.status_code == 422:
            return await _standby_form(request, db, name, form, status_code=422, error=err.detail,
                                       headers={"HX-Retarget": "#standby-settings-form", "HX-Reswap": "outerHTML"})
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _done("Pengaturan database cadangan disimpan.")


@router.get("/containers/{name}/cadangan/restore")
async def standby_restore_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    cfg = await runner_view.container_settings(db, name)
    return templates.TemplateResponse(request=request, name="runners/_standby_restore.html", context={
        "name": name, "runner_names": sorted(runner_registry.entries().active),
        "current": cfg.standby_runner if cfg else None})


@router.post("/containers/{name}/cadangan/restore")
async def standby_restore_now(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    """Restore sekarang. Tidak pernah memulihkan ke database produksi, dan tidak menunggu jadwal."""
    form = await request.form()
    runner = str(form.get("runner", ""))
    if runner not in runner_registry.entries().active:
        raise HTTPException(status_code=422, detail="Pilih runner yang aktif.")
    async with db.begin():
        beat_machine = await db.scalar(select(RunnerHeartbeat.machine_id).where(RunnerHeartbeat.runner == runner))
        own_machine = await container_service.machine_id(db, name)
    if beat_machine and own_machine and beat_machine == own_machine:
        raise HTTPException(status_code=409, detail="Runner ini satu server dengan container; pilih runner di server lain.")
    try:
        await standby_service.create_full_job(db, name, runner, requested_by=user.username)
    except runner_service.RunnerError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _done(f"Restore diminta, menunggu runner {runner}.")


async def _standby_snapshot(db, name: str) -> dict | None:
    """Nilai biasa, bukan objek ORM: objek kedaluwarsa begitu transaksi berikutnya selesai atau dibatalkan."""
    async with db.begin():
        state = await runner_view.standby_state(db, name)
        if state is None or not state.runner:
            return None
        return {"runner": state.runner, "data_until": state.data_until}


@router.get("/containers/{name}/cadangan/promote")
async def standby_promote_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    state = await _standby_snapshot(db, name)
    if state is None:
        raise HTTPException(status_code=409, detail="Belum ada database cadangan yang siap dinaikkan.")
    return templates.TemplateResponse(request=request, name="runners/_standby_promote.html", context={
        "name": name, "standby": state, "error": None, "new_name": "", "port": 3306})


@router.post("/containers/{name}/cadangan/promote")
async def standby_promote(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    """Naikkan ke production. Tidak bisa dibatalkan, jadi butuh nama baru dan konfirmasi tertulis."""
    form = await request.form()
    new_name = str(form.get("new_name", "")).strip()
    raw_port = str(form.get("port", "")).strip()
    state = await _standby_snapshot(db, name)
    runner_name = state["runner"] if state else ""
    error = None
    if form.get("confirm") != "yes":
        error = "Centang konfirmasi bahwa aksi ini tidak bisa dibatalkan."
    elif not raw_port.isdigit():
        error = "Port harus berupa angka."
    else:
        try:
            await promote_service.create(db, name, new_name, int(raw_port), user.username)
        except NameRejected as err:
            error = str(err)
        except AgentsFileWriteError:
            error = "Tidak bisa menulis config/agents.conf. Tidak ada yang berubah; perbaiki izin file."
        except runner_service.RunnerError as err:
            error = err.detail
    if error:
        return templates.TemplateResponse(
            request=request, name="runners/_standby_promote.html", status_code=422,
            headers={"HX-Retarget": "#promote-form", "HX-Reswap": "outerHTML"},
            context={"name": name, "standby": state, "error": error, "new_name": new_name, "port": raw_port})
    return _done(f"Penaikan diminta: {new_name} akan berjalan di runner {runner_name}.")


# ---------- Restore manual ----------
@router.get("/containers/{name}/restore-manual")
async def container_manual(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    context = await _detail_context(db, user, name, "manual")
    context.update(sessions=await manual_service.sessions_for(db, name),
                   choices=await manual_service.full_choices(db, name), max_hours=settings.MANUAL_RESTORE_MAX_HOURS)
    return render(request, "dashboard/container.html", "manual/_container_manual.html", context)


async def _manual_form(request: Request, db, name: str, *, form=None, error: str | None = None, status_code: int = 200):
    choices = await manual_service.full_choices(db, name)
    age = await db.scalar(select(func.timestampdiff(literal_column("SECOND"), Heartbeat.at, func.now()))
                          .where(Heartbeat.container == name))
    source_alive = age is not None and age <= settings.ALERT_HEARTBEAT_MINUTES * 60      # sumber masih menerima tulisan
    headers = {"HX-Retarget": "#manual-form", "HX-Reswap": "outerHTML"} if error else {}
    return templates.TemplateResponse(
        request=request, name="manual/_new_dialog.html", status_code=status_code, headers=headers,
        context={"name": name, "choices": choices, "error": error, "source_alive": source_alive,
                 "hours": [h for h in manual_service.HOURS_CHOICES if h <= settings.MANUAL_RESTORE_MAX_HOURS],
                 "form": {k: str(form.get(k, "")) for k in ("full", "binlog", "new_name", "port", "hours")} if form else
                 {"full": "", "binlog": "all", "new_name": "", "port": "3306", "hours": "4"}})


@router.get("/containers/{name}/restore-manual/new")
async def manual_new_dialog(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    return await _manual_form(request, db, name)


@router.post("/containers/{name}/restore-manual")
async def manual_create(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    try:
        full_id, port, hours = int(str(form.get("full", ""))), int(str(form.get("port", ""))), int(str(form.get("hours", "")))
    except ValueError:
        return await _manual_form(request, db, name, form=form, error="Pilih full, port, dan batas waktu.", status_code=422)
    try:
        row, token = await manual_service.create(
            db, name, full_id, str(form.get("binlog", "")), str(form.get("new_name", "")).strip(), port, hours, user.username)
    except manual_service.RestoreError as err:
        if err.status_code == 404:
            raise HTTPException(status_code=404, detail=err.detail) from err
        return await _manual_form(request, db, name, form=form, error=err.detail, status_code=422 if err.status_code < 500 else err.status_code)
    url = _base_url(request)
    return templates.TemplateResponse(request=request, name="manual/_created.html", context={
        "name": name, "row": row, "command": manual_service.command(url, token), "url": url})


@router.post("/restore-manual/{restore_id}/extend")
async def manual_extend(restore_id: int, user: CurrentUserDep, db: DbSession):
    try:
        await manual_service.extend(db, restore_id, user.username)
    except manual_service.RestoreError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _done("Sesi diperpanjang 2 jam.", modal=False)


@router.post("/restore-manual/{restore_id}/revoke")
async def manual_revoke(restore_id: int, user: CurrentUserDep, db: DbSession):
    try:
        await manual_service.revoke(db, restore_id, user.username)
    except manual_service.RestoreError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return _done("Sesi dicabut. Token tidak berlaku lagi.", modal=False)


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

