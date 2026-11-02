from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.database import DbSession
from app.nas import service

router = APIRouter(prefix="/api", tags=["nas"])


@router.get("/watchdog")
async def watchdog(db: DbSession):
    """Untuk pemeriksa di luar VM: alert tidak bisa melaporkan matinya LumbungDB sendiri. Tanpa rahasia dan tanpa login.
    Membaca hasil terakhir, tidak menyentuh NAS. 503 bila pengecek berhenti."""
    view = await service.get_view(db)
    return JSONResponse({"nas": view.state}, status_code=503 if view.stale else 200)
