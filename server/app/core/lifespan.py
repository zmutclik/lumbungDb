from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.database import engine
from app.core.discovery import import_domain_modules


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Hook start domain: agents.conf yang tidak valid menggagalkan start dengan pesan bernomor baris.
    for module in import_domain_modules("startup"):
        module.on_startup()
    app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        timeout=httpx.Timeout(5.0, connect=2.0),
    )

    yield

    await app.state.http_client.aclose()
    await engine.dispose()
