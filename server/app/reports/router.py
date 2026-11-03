from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.alerts import service as alert_service
from app.auth.dependencies import CurrentUserDep, require_csrf, require_ready, require_role
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
    return render(request, "reports/reports.html", "reports/_reports.html", {**base, "kind": kind, "reports": rows})


@router.get("/reports/{report_id}")
async def report_page(request: Request, report_id: int, user: CurrentUserDep, db: DbSession):
    base = await _base(db, user, "reports")
    report = await db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Laporan tidak ada.")
    return render(request, "reports/report.html", "reports/_report.html", {**base, "report": report})
