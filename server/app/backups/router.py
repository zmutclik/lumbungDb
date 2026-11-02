from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.requests import ClientDisconnect

from app.backups import naming, service
from app.core.database import DbSession
from app.nas import service as nas_service
from app.agents.dependencies import AgentName

router = APIRouter(prefix="/api", tags=["backups"])

_active_uploads = 0   # per proses web: jatah dibagi rata, lihat service.PROCESS_SLOTS


@router.put("/upload/{kind}/{relpath:path}")
async def upload(
    kind: str, relpath: str, request: Request, container: AgentName, db: DbSession,
    x_sha256: Annotated[str | None, Header()] = None,
    x_file_mtime: Annotated[str | None, Header()] = None,
    x_backup_request: Annotated[str | None, Header()] = None,
    x_binlog_start: Annotated[str | None, Header()] = None,
):
    global _active_uploads
    try:
        path = naming.parse_upload_path(kind, relpath)
    except naming.InvalidPath as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    async with db.begin():
        nas = await nas_service.get_view(db)
    if not nas.writable:
        raise HTTPException(status_code=503, detail="NAS tidak ter-mount atau tidak merespons.",
                            headers={"Retry-After": "60"})
    if _active_uploads >= service.PROCESS_SLOTS:
        raise HTTPException(status_code=503, detail="Batas upload bersamaan tercapai.", headers={"Retry-After": "30"})

    request_id = (
        int(x_backup_request) if x_backup_request and x_backup_request.isdigit() and len(x_backup_request) <= 18
        else None
    )
    _active_uploads += 1
    try:
        result = await service.receive_upload(
            db, container, path, request.stream(),
            expected_sha256=x_sha256, mtime_header=x_file_mtime, request_id=request_id,
            binlog_start=naming.valid_binlog_start(x_binlog_start),
        )
    except service.UploadRejected as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    except ClientDisconnect as err:
        raise HTTPException(status_code=400, detail="Koneksi terputus.") from err
    finally:
        _active_uploads -= 1
    return {"result": result.result, "stored_path": result.stored_path}
