import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.agents.agents_file import AgentsFileInvalid, AgentsFileWriteError, NameRejected
from app.alerts import service as alert_service
from app.auth.dependencies import CurrentUserDep, require_csrf, require_ready, require_role
from app.core.database import DbSession
from app.core.templating import render, templates
from app.runner_admin import service
from app.runner_installs import service as install_service
from app.runners.dependencies import registry as runners

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["runner-admin"], dependencies=[Depends(require_csrf), Depends(require_ready), Depends(require_role("admin"))],
)


async def _base(db, user) -> dict:
    _states, nas, alerts, since, now = await alert_service.current_alerts(db)
    return {"user": user, "active": "runners", "nas": nas, "alerts": alerts, "alert_since": since, "now": now}


def _done(message: str) -> Response:
    events = {"close-modal": True, "show-toast": message, "live-refresh": True}
    return Response(status_code=200, headers={"HX-Trigger": json.dumps(events), "HX-Reswap": "none"})


@router.get("/runners")
async def runners_page(request: Request, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user)
    jobs = await service.recent_jobs(db, limit=20)
    return render(request, "runners/runners.html", "runners/_runners.html", {
        **base, "rows": await service.runner_rows(db), "jobs": jobs,
        "results": await service.results_by_job(db, [j.id for j in jobs]),
    })


@router.get("/runners/new")
async def new_runner_dialog(request: Request, user: CurrentUserDep):
    return templates.TemplateResponse(request=request, name="runners/_new_dialog.html", context={"name": "", "error": None})


def _url(request: Request) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


@router.post("/runners")
async def create_runner(request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    name = str(form.get("name", "")).strip()[:100]
    error = runners.name_error(name)
    token = None
    if error is None:
        try:
            token = runners.add(name)
        except NameRejected as err:
            error = str(err)
        except AgentsFileWriteError as err:
            error = f"{err} Runner belum didaftarkan."
    if error:
        return templates.TemplateResponse(
            request=request, name="runners/_new_dialog.html", context={"name": name, "error": error},
            status_code=422, headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})
    url = _url(request)
    # Token runner sudah tersimpan di runners.conf; bila perintah pasang gagal dibuat, isi manual tetap tampil dan
    # perintah baru bisa dibuat dari halaman Runner.
    install_command = expires_at = None
    try:
        install_token, expires_at = await install_service.create_command(db, name, user.username)
        install_command = install_service.install_command(url, install_token)
    except Exception:
        logger.exception("runner_install_command_failed", extra={"runner": name})
    return templates.TemplateResponse(request=request, name="runners/_new_token.html", context={
        "name": name, "env": install_service.env_text(token, url), "install_command": install_command,
        "expires_at": expires_at, "base_url": url})


@router.post("/runners/{name}/install-command")
async def renew_install_command(name: str, request: Request, user: CurrentUserDep, db: DbSession):
    try:
        install_token, expires_at = await install_service.renew_command(db, name, user.username)
    except install_service.InstallError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    url = _url(request)
    return templates.TemplateResponse(request=request, name="runners/_install_renewed.html", context={
        "name": name, "install_command": install_service.install_command(url, install_token),
        "expires_at": expires_at, "base_url": url})


@router.get("/runners/{name}/disable")
async def disable_dialog(name: str, request: Request, user: CurrentUserDep):
    if name not in runners.entries().active:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request=request, name="runners/_disable_dialog.html", context={"name": name})


@router.post("/runners/{name}/disable")
async def disable_runner(name: str, user: CurrentUserDep, db: DbSession):
    if name not in runners.entries().active:
        raise HTTPException(status_code=404)
    try:
        runners.disable(name)
    except AgentsFileWriteError as err:
        status = 409 if isinstance(err, AgentsFileInvalid) else 500   # 5xx tidak menampilkan pesan
        raise HTTPException(status_code=status, detail=f"{err} Runner belum dinonaktifkan.") from err
    await install_service.forget(db, name)
    return _done(f"Runner {name} dinonaktifkan.")
