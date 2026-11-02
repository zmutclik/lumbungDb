from fastapi import APIRouter, HTTPException, Request

from app.backup_control import service
from app.backup_control.schemas import HeartbeatIn, RequestStatusIn
from app.core.database import DbSession
from app.core.dependencies import client_ip
from app.agents.dependencies import AgentName

router = APIRouter(prefix="/api", tags=["agent"])


@router.post("/heartbeat")
async def heartbeat(data: HeartbeatIn, request: Request, container: AgentName, db: DbSession):
    return await service.record_heartbeat(db, container, data, client_ip(request))


@router.post("/agent/requests/{request_id}/status")
async def request_status(request_id: int, data: RequestStatusIn, container: AgentName, db: DbSession):
    try:
        await service.report_status(db, container, request_id, data)
    except service.ControlError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    return {"status": data.status}
