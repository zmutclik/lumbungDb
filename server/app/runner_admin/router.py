import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.agents.agents_file import AgentsFileWriteError, NameRejected
from app.alerts import service as alert_service
from app.auth.dependencies import CurrentUserDep, require_csrf, require_ready, require_role
from app.core.database import DbSession
from app.core.templating import render, templates
from app.runner_admin import service
from app.runners.dependencies import registry as runners

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


@router.post("/runners")
async def create_runner(request: Request, user: CurrentUserDep):
    form = await request.form()
    name = str(form.get("name", "")).strip()[:100]
    error = runners.name_error(name)
    token = None
    if error is None:
        try:
            token = runners.add(name)
        except NameRejected as err:
            error = str(err)
        except AgentsFileWriteError:
            error = "Tidak bisa menulis config/runners.conf. Runner belum didaftarkan; perbaiki izin file."
    if error:
        return templates.TemplateResponse(
            request=request, name="runners/_new_dialog.html", context={"name": name, "error": error},
            status_code=422, headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})
    return templates.TemplateResponse(request=request, name="runners/_new_token.html", context={
        "name": name, "token": token, "url": f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"})


@router.get("/runners/{name}/disable")
async def disable_dialog(name: str, request: Request, user: CurrentUserDep):
    if name not in runners.entries().active:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(request=request, name="runners/_disable_dialog.html", context={"name": name})


@router.post("/runners/{name}/disable")
async def disable_runner(name: str, user: CurrentUserDep):
    if name not in runners.entries().active:
        raise HTTPException(status_code=404)
    try:
        runners.disable(name)
    except AgentsFileWriteError as err:
        raise HTTPException(status_code=500, detail="Tidak bisa menulis config/runners.conf.") from err
    return _done(f"Runner {name} dinonaktifkan.")
