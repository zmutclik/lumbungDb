"""Proses pengecek: memeriksa NAS lewat proses kecil, menandai permintaan kedaluwarsa, dan menilai alert tiap menit.
Satu instance saja:  python -m app.alerts.worker
Proses ini tidak pernah menyentuh NAS sendiri, jadi NFS yang macet tidak menghentikan alert."""
import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.alerts import service as alert_service
from app.auth.cleanup import purge_auth_rows
from app.backup_control import service as control_service
from app.containers import service as container_service
from app.core.config import settings
from app.core.database import make_engine
from app.core.discovery import import_domain_modules
from app.core.logging import setup_logging
from app.nas import service as nas_service
from app.nas.monitor import NasMonitor
from app.agents.dependencies import registry
from app.reports import ingest as metrics_ingest
from app.reports import service as report_service
from app.retention import service as retention_service
from app.runners import scheduling as runner_scheduling
from app.runners import service as runner_service
from app.runners.dependencies import registry as runner_registry

logger = logging.getLogger(__name__)
PURGE_EVERY = 60     # putaran; sekitar tiap jam


async def tick(factory: async_sessionmaker, monitor: NasMonitor, client: httpx.AsyncClient) -> None:
    result = await monitor.check()
    async with factory() as db:
        await nas_service.save_result(db, result)
        await container_service.sync_with_file(db)
        await control_service.expire_requests(db)
        await runner_service.expire_jobs(db)
        await runner_scheduling.ensure_defaults(db)
        await runner_scheduling.schedule_due_tests(db)
        await runner_scheduling.schedule_standby(db)
        await alert_service.run_check(db, client)
        await report_service.publish_due(db, client)


async def main() -> None:
    setup_logging()
    import_domain_modules("models")
    registry.load()
    runner_registry.load()
    engine = make_engine(pool_size=2, max_overflow=0, statement_timeout=30)
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    monitor = NasMonitor()
    deleter = retention_service.Deleter()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    rounds = 0
    try:
        async with httpx.AsyncClient() as client:
            while not stop.is_set():
                try:
                    await tick(factory, monitor, client)
                    if rounds % PURGE_EVERY == 0:
                        await purge_auth_rows(factory)
                        async with factory() as db:
                            cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=settings.METRICS_RETENTION_DAYS)
                            await metrics_ingest.purge(db, cutoff)
                        async with factory() as db:
                            await retention_service.run_retention(db, deleter)
                except Exception:      # database sesaat tidak terjangkau tidak boleh menghentikan pengecek
                    logger.exception("check_failed")
                rounds += 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.CHECK_INTERVAL_SECONDS)
                except TimeoutError:
                    pass
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
