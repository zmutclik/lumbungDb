import re
import time
from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.backups import storage
from app.core.config import settings
from app.core.database import DbSession
from app.core.dependencies import client_ip
from app.runners import service
from app.runners.dependencies import RunnerName
from app.runners.schemas import JobResultIn, ProgressIn, RunnerHeartbeatIn

router = APIRouter(prefix="/api/runner", tags=["runner"])

_RANGE = re.compile(r"^bytes=(\d{1,18})-$")
CHUNK = 1024 * 1024
_active_downloads = 0    # per proses web, seperti batas upload
_limiter = anyio.CapacityLimiter(max(1, settings.RUNNER_DOWNLOAD_MAX_CONCURRENT))


def _raise(err: service.RunnerError) -> HTTPException:
    headers = {"Retry-After": "60"} if err.status_code == 503 else None
    return HTTPException(status_code=err.status_code, detail=err.detail, headers=headers)


@router.post("/heartbeat")
async def heartbeat(data: RunnerHeartbeatIn, runner: RunnerName, db: DbSession):
    await service.record_heartbeat(db, runner, data)
    return {"status": "ok"}


@router.post("/jobs/claim")
async def claim(runner: RunnerName, db: DbSession):
    return {"job": await service.claim_next(db, runner)}


@router.post("/jobs/{job_id}/progress")
async def progress(job_id: int, data: ProgressIn, runner: RunnerName, db: DbSession):
    try:
        await service.report_progress(db, runner, job_id, data.step)
    except service.RunnerError as err:
        raise _raise(err) from err
    return {"status": "ok"}


@router.post("/jobs/{job_id}/result")
async def result(job_id: int, data: JobResultIn, runner: RunnerName, db: DbSession):
    try:
        await service.finish_job(db, runner, job_id, data)
    except service.RunnerError as err:
        raise _raise(err) from err
    return {"status": data.status}


def _read(handle, size: int) -> bytes:
    return handle.read(size)


async def _stream(path, offset: int, length: int) -> AsyncIterator[bytes]:
    """Semua I/O NAS lewat thread, supaya NFS yang macet tidak menghentikan event loop."""
    global _active_downloads
    per_second = settings.RUNNER_DOWNLOAD_MAX_MBPS * 125_000
    _active_downloads += 1
    try:
        handle = await anyio.to_thread.run_sync(open, path, "rb", limiter=_limiter, abandon_on_cancel=True)
        try:
            await anyio.to_thread.run_sync(handle.seek, offset, limiter=_limiter, abandon_on_cancel=True)
            remaining = length
            while remaining > 0:
                started = time.monotonic()
                chunk = await anyio.to_thread.run_sync(_read, handle, min(CHUNK, remaining), limiter=_limiter,
                                                       abandon_on_cancel=True)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
                wait = len(chunk) / per_second - (time.monotonic() - started)
                if wait > 0:
                    await anyio.sleep(wait)
        finally:
            handle.close()
    finally:
        _active_downloads -= 1


@router.get("/jobs/{job_id}/files/{backup_id}")
async def download(job_id: int, backup_id: int, request: Request, runner: RunnerName, db: DbSession,
                   range_header: Annotated[str | None, Header(alias="Range")] = None):
    match = _RANGE.fullmatch(range_header) if range_header else None
    if range_header and not match:
        raise HTTPException(status_code=416, detail="Hanya mendukung Range bytes=N-.")
    offset = int(match.group(1)) if match else 0
    if _active_downloads >= settings.RUNNER_DOWNLOAD_MAX_CONCURRENT:
        raise HTTPException(status_code=503, detail="Batas unduhan bersamaan tercapai.", headers={"Retry-After": "30"})
    try:
        target = await service.download_target(db, runner, job_id, backup_id, offset, client_ip(request))
    except service.RunnerError as err:
        raise _raise(err) from err
    if offset and offset >= target.size:
        raise HTTPException(status_code=416, detail="Range di luar ukuran file.",
                            headers={"Content-Range": f"bytes */{target.size}"})
    root = storage.container_root(target.container)
    path = (root / target.stored_path).resolve()
    if not path.is_relative_to(root):      # pengaman kedua; stored_path dibuat server
        raise HTTPException(status_code=403, detail="Lokasi file tidak valid.")
    length = target.size - offset
    headers = {"Content-Length": str(length), "Accept-Ranges": "bytes", "X-SHA256": target.sha256}
    if offset:
        headers["Content-Range"] = f"bytes {offset}-{target.size - 1}/{target.size}"
    return StreamingResponse(_stream(path, offset, length), status_code=206 if offset else 200,
                             media_type="application/octet-stream", headers=headers)
