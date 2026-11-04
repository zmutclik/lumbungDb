import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.alerts import service as alert_service
from app.auth.dependencies import CurrentUserDep, require_csrf, require_ready, require_role
from app.ai import qa as ai_qa
from app.ai import service as ai_service
from app.ai.providers import get_provider
from app.core.database import DbSession
from app.core.templating import render
from app.reports.models import Report

router = APIRouter(
    tags=["reports"], dependencies=[Depends(require_csrf), Depends(require_ready), Depends(require_role("admin"))],
)


async def _base(db, user, active: str) -> dict:
    _states, nas, alerts, since, now = await alert_service.current_alerts(db)
    return {"user": user, "active": active, "nas": nas, "alerts": alerts, "alert_since": since, "now": now}


@router.get("/reports")
async def reports_page(request: Request, user: CurrentUserDep, db: DbSession, jenis: str = "daily"):
    kind = jenis if jenis in ("daily", "weekly") else "daily"
    base = await _base(db, user, "reports")
    rows = (await db.scalars(
        select(Report).where(Report.kind == kind).order_by(Report.period_start.desc()).limit(60))).all()
    qa = await ai_qa.history(db, user.username) if get_provider() is not None else None
    return render(request, "reports/reports.html", "reports/_reports.html",
                  {**base, "kind": kind, "reports": rows, "qa": qa, "qa_error": None})


@router.post("/reports/ask")
async def ask_ai(request: Request, user: CurrentUserDep, db: DbSession):
    """Tanya-jawab atas data laporan. Hanya membaca metrik teragregasi; jawaban tersimpan untuk audit."""
    if get_provider() is None:
        raise HTTPException(status_code=409, detail="Lapisan AI belum dinyalakan (AI_PROVIDER).")
    form = await request.form()
    error = None
    await db.rollback()
    try:
        async with httpx.AsyncClient() as client:
            await ai_qa.ask(db, client, user.username, str(form.get("question", "")))
    except ai_qa.QaError as err:
        error = err.detail
    history = await ai_qa.history(db, user.username)
    return render(request, "reports/reports.html", "reports/_qa.html", {"qa": history, "qa_error": error},
                  status_code=422 if error else 200)


@router.get("/reports/{report_id}")
async def report_page(request: Request, report_id: int, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "reports")
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Laporan tidak ada.")
    ai = await ai_service.for_report(db, report_id)
    return render(request, "reports/report.html", "reports/_report.html",
                  {**base, "report": report, "ai": ai, "ai_enabled": get_provider() is not None})


@router.post("/reports/{report_id}/ai")
async def regenerate_ai(report_id: int, user: CurrentUserDep, db: DbSession):
    """Membuat ulang ringkasan AI, mis. sesudah kegagalan. Hanya mengganti ringkasan; laporan tidak berubah."""
    if get_provider() is None:
        raise HTTPException(status_code=409, detail="Lapisan AI belum dinyalakan (AI_PROVIDER).")
    if await db.get(Report, report_id) is None:
        raise HTTPException(status_code=404, detail="Laporan tidak ada.")
    await db.rollback()
    async with httpx.AsyncClient() as client:
        await ai_service.summarize(db, client, report_id, force=True)
    return Response(status_code=200, headers={"HX-Refresh": "true"})
