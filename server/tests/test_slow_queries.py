"""Log slow query: selisih per jam dari penghitung performance_schema, tab di detail container, CSV, dan kosongkan."""
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.agents.dependencies import registry
from app.backup_control.models import ControlLog
from app.core.database import async_session_factory
from app.nas.monitor import ProbeResult
from app.nas.service import save_result
from app.reports import service as reports
from app.slow_queries import service as slow_service
from app.slow_queries.models import SlowQueryCursor, SlowQueryLog
from tests.conftest import login, make_user
from tests.test_control import beat
from tests.test_reports import metrics

NAME = "siak-db"
SECOND = 10**12
SLOW_TEXT = "SELECT * FROM `pasien` WHERE `nama` LIKE ?"


def slow(count, total_s, digest="d1", text=SLOW_TEXT, database="siak", **extra):
    return {"database": database, "digest": digest, "text": text, "count": count, "total_ps": int(total_s * SECOND),
            "max_ps": 5 * SECOND, "rows_examined": count * 1000, "rows_sent": count, "no_index_used": count,
            "no_good_index_used": 0, "full_join": 0, "tmp_disk_tables": 0, **extra}


@pytest.fixture
async def agent():
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 1000, 800))
    return {"X-Token": registry.add(NAME)}


def with_slow(items, uptime=1000):
    return {**metrics(uptime=uptime), "slow": items}


async def logged():
    async with async_session_factory() as db:
        return list(await db.scalars(select(SlowQueryLog).order_by(SlowQueryLog.digest)))


async def test_first_report_is_baseline_then_deltas_are_logged(client, agent):
    await beat(client, agent, metrics=with_slow([slow(100, 300)]))
    assert await logged() == []                                          # riwayat sejak start: hanya acuan
    await beat(client, agent, metrics=with_slow([slow(110, 330), slow(3, 0.3, digest="d2")], uptime=1100))
    rows = await logged()
    assert [(r.digest, r.count, r.total_ps) for r in rows] == [("d1", 10, 30 * SECOND)]   # d2 cepat: tidak dicatat
    assert rows[0].rows_examined == 10_000 and rows[0].no_index_used == 10
    await beat(client, agent, metrics=with_slow([slow(112, 340)], uptime=1200))           # jam yang sama: dijumlah
    assert (await logged())[0].count == 12


async def test_restart_counts_new_values_and_text_is_normalized(client, agent):
    await beat(client, agent, metrics=with_slow([slow(100, 300)]))
    leaky = "SELECT * FROM pasien WHERE nama = 'BUDI' AND nik = 3301234567890"
    await beat(client, agent, metrics=with_slow([slow(4, 8, text=leaky)], uptime=50))     # MariaDB restart
    rows = await logged()
    assert rows[0].count == 4
    assert "BUDI" not in rows[0].text and "3301234567890" not in rows[0].text
    async with async_session_factory() as db:
        cursor = await db.get(SlowQueryCursor, (NAME, "siak", "d1"))
    assert (cursor.count, cursor.uptime) == (4, 50)


async def test_invalid_items_are_skipped(client, agent):
    await beat(client, agent, metrics=with_slow([slow(1, 2)]))
    bad = [{"digest": "x"}, slow(-1, 2, digest="neg"), {**slow(1, 9, digest="t"), "count": "10"}, slow(5, 20, digest="d1")]
    await beat(client, agent, metrics=with_slow(bad, uptime=1100))
    assert [(r.digest, r.count) for r in await logged()] == [("d1", 4)]


async def test_tab_csv_clear_and_report(client, agent):
    await beat(client, agent, metrics=with_slow([slow(100, 300)]))
    await beat(client, agent, metrics=with_slow([slow(110, 330)], uptime=1100))
    await make_user()
    csrf = await login(client)
    page = await client.get(f"/containers/{NAME}/slow-query")
    assert page.status_code == 200 and "SELECT * FROM `pasien`" in page.text and "tanpa index" in page.text
    assert (await client.get(f"/containers/{NAME}/slow-query?hari=30")).status_code == 200

    csv = await client.get(f"/containers/{NAME}/slow-query.csv?hari=30")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("database,pola_query,eksekusi") and lines[1].startswith("siak,") and ",10,30.000,3.000," in lines[1]

    async with async_session_factory() as db, db.begin():
        data = await reports.build(db, "monthly", datetime(2000, 1, 1), datetime(2100, 1, 1))
    assert data["containers"][0]["stats"]["slow_patterns"][0]["count"] == 10

    assert (await client.post(f"/containers/{NAME}/slow-query/clear")).status_code == 403    # tanpa CSRF
    done = await client.post(f"/containers/{NAME}/slow-query/clear",
                             headers={"X-CSRF-Token": csrf, "HX-Request": "true"})
    assert done.status_code == 200 and "dikosongkan" in done.headers["hx-trigger"]
    assert await logged() == []
    async with async_session_factory() as db:
        entry = await db.scalar(select(ControlLog).where(ControlLog.action == slow_service.CLEAR_ACTION))
    assert entry.actor == "alice" and entry.detail == "1 baris per jam dihapus"
    # Acuan tetap: kiriman berikutnya hanya mencatat eksekusi baru, bukan seluruh riwayat lagi.
    await beat(client, agent, metrics=with_slow([slow(111, 333)], uptime=1200))
    assert [(r.count) for r in await logged()] == [1]
    assert "dikosongkan" in (await client.get(f"/containers/{NAME}/slow-query")).text


async def test_unknown_container_and_purge(client, agent):
    await make_user()
    await login(client)
    assert (await client.get("/containers/tidak-ada/slow-query")).status_code == 404
    await beat(client, agent, metrics=with_slow([slow(1, 2)]))
    await beat(client, agent, metrics=with_slow([slow(2, 4)], uptime=1100))
    async with async_session_factory() as db, db.begin():
        await slow_service.purge(db, datetime(2100, 1, 1))
    async with async_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(SlowQueryLog)) == 0
