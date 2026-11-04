"""Endpoint untuk script Restore manual. Tanpa login: yang dipakai token sesi (X-Restore-Token) dan machine-id."""
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.agents.agents_file import AgentsFileWriteError, NameRejected
from app.backups import storage
from app.core.config import settings
from app.core.database import DbSession
from app.core.dependencies import client_ip
from app.manual_restore import service
from app.runners import router as runner_router

router = APIRouter(tags=["manual-restore"])

_SCRIPT = (Path(__file__).parent / "files" / "restore.sh").read_text()

Token = Annotated[str | None, Header(alias="X-Restore-Token")]
Machine = Annotated[str | None, Header(alias="X-Machine-Id")]


def _text(err: service.RestoreError) -> PlainTextResponse:
    headers = {"Retry-After": "60"} if err.status_code in (429, 503) else None
    return PlainTextResponse(err.detail + "\n", status_code=err.status_code, headers=headers)


# Tanpa login dan tanpa rahasia: admin bisa membacanya dulu sebelum dijalankan.
@router.get("/restore.sh", include_in_schema=False)
async def restore_script():
    return PlainTextResponse(_SCRIPT)


@router.post("/api/manual/session", include_in_schema=False)
async def manual_session(request: Request, db: DbSession, x_restore_token: Token = None, x_machine_id: Machine = None):
    """Jawaban teks biasa, termasuk saat ditolak: script memakai sh dan curl saja."""
    try:
        return PlainTextResponse(await service.claim(db, x_restore_token, x_machine_id, client_ip(request)))
    except service.RestoreError as err:
        return _text(err)


@router.get("/api/manual/files/{backup_id}", include_in_schema=False)
async def manual_download(backup_id: int, request: Request, db: DbSession, x_restore_token: Token = None,
                          x_machine_id: Machine = None, range_header: Annotated[str | None, Header(alias="Range")] = None):
    match = runner_router._RANGE.fullmatch(range_header) if range_header else None
    if range_header and not match:
        raise HTTPException(status_code=416, detail="Hanya mendukung Range bytes=N-.")
    offset = int(match.group(1)) if match else 0
    if runner_router._active_downloads >= settings.RUNNER_DOWNLOAD_MAX_CONCURRENT:
        raise HTTPException(status_code=503, detail="Batas unduhan bersamaan tercapai.", headers={"Retry-After": "30"})
    try:
        container, stored_path, size, sha256 = await service.download_target(
            db, x_restore_token, x_machine_id, client_ip(request), backup_id, offset)
    except service.RestoreError as err:
        raise HTTPException(status_code=err.status_code, detail=err.detail,
                            headers={"Retry-After": "60"} if err.status_code in (429, 503) else None) from err
    if offset and offset >= size:
        raise HTTPException(status_code=416, detail="Range di luar ukuran file.", headers={"Content-Range": f"bytes */{size}"})
    root = storage.container_root(container)
    path = (root / stored_path).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(status_code=403, detail="Lokasi file tidak valid.")
    length = size - offset
    headers = {"Content-Length": str(length), "Accept-Ranges": "bytes", "X-SHA256": sha256}
    if offset:
        headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
    return StreamingResponse(runner_router._stream(path, offset, length), status_code=206 if offset else 200,
                             media_type="application/octet-stream", headers=headers)


@router.post("/api/manual/progress", include_in_schema=False)
async def manual_progress(request: Request, db: DbSession, x_restore_token: Token = None, x_machine_id: Machine = None):
    """Isi: `ok <langkah>` atau `fail <alasan>`."""
    body = (await request.body())[:1000].decode("utf-8", errors="replace").strip()
    kind, _, text = body.partition(" ")
    if kind not in ("ok", "fail"):
        return PlainTextResponse("Format: ok <langkah> atau fail <alasan>\n", status_code=422)
    try:
        await service.progress(db, x_restore_token, x_machine_id, client_ip(request), text, kind == "fail")
    except service.RestoreError as err:
        return _text(err)
    return PlainTextResponse("ok\n")


@router.post("/api/manual/register", include_in_schema=False)
async def manual_register(request: Request, db: DbSession, x_restore_token: Token = None, x_machine_id: Machine = None):
    try:
        done = await service.register(db, x_restore_token, x_machine_id, client_ip(request))
    except service.RestoreError as err:
        return _text(err)
    except AgentsFileWriteError:
        return PlainTextResponse("config/agents.conf tidak bisa ditulis di LumbungDB. Perbaiki izinnya, lalu jalankan "
                                 "perintah yang sama lagi; database di server ini tidak dipulihkan ulang.\n", status_code=500)
    except NameRejected as err:
        return PlainTextResponse(f"{err}\n", status_code=409)
    return PlainTextResponse(f"LUMBUNG_TOKEN={done.token}\nLUMBUNG_AGE_RECIPIENTS={done.age_recipients}\n")
