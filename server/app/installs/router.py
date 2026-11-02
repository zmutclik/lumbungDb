from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse

from app.core.database import DbSession
from app.installs import service

router = APIRouter(tags=["installs"])

_FILES = Path(__file__).parent / "files"
_SCRIPT = (_FILES / "install.sh").read_text()
_COMPOSE = (_FILES / "docker-compose.lumbung.yml").read_text()


# Tanpa login dan tanpa rahasia: admin bisa membacanya dulu sebelum dijalankan.
@router.get("/install.sh", include_in_schema=False)
async def install_script():
    return PlainTextResponse(_SCRIPT)


@router.get("/install/docker-compose.lumbung.yml", include_in_schema=False)
async def install_compose():
    return PlainTextResponse(_COMPOSE)


@router.post("/api/install/session", include_in_schema=False)
async def install_session(
    request: Request, db: DbSession,
    x_install_token: Annotated[str | None, Header()] = None,
    x_machine_id: Annotated[str | None, Header()] = None,
):
    """Jawaban teks biasa, termasuk saat ditolak: script memakai sh dan curl saja, tanpa pengurai JSON."""
    url = f"{request.url.scheme}://{request.headers.get('host', '')}"
    try:
        return PlainTextResponse(await service.claim(db, x_install_token, x_machine_id, url))
    except service.InstallError as err:
        return PlainTextResponse(err.detail + "\n", status_code=err.status_code)
