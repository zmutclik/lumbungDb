"""Ringkasan naratif laporan. AI hanya menafsirkan: angka dan temuan sudah dihitung aturan biasa di app.reports, dan
ringkasan ini tidak bisa mengubah atau menekan alert. Bila penyedia gagal, laporan tetap terbit tanpa ringkasan."""
import json
import logging
from datetime import UTC

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AiSummary
from app.ai.normalize import normalize_query
from app.ai.providers import Provider, ProviderError, get_provider
from app.containers.models import Heartbeat
from app.reports.models import Report

logger = logging.getLogger(__name__)
MAX_TEXT = 4000
# Hanya angka dan metadata. Daftar putih: kunci lain di stats laporan tidak ikut terkirim.
STAT_KEYS = ("connections_peak", "max_connections", "refused", "hit_ratio", "slow_queries", "deadlocks", "disk_free",
             "disk_full_days", "full_size")

SYSTEM = (
    "Kamu membantu admin database rumah sakit membaca laporan kesehatan MariaDB. Masukanmu JSON berisi angka, temuan "
    "yang sudah dihitung aturan biasa, dan pola query yang nilainya sudah dibuang (diganti ?). Tulis ringkasan "
    "dalam bahasa Indonesia, paling banyak enam butir pendek: apa yang terjadi, seberapa penting, dan langkah berikutnya. "
    "Aturan: hanya pakai data di masukan, jangan mengarang angka atau nama; jangan pernah menyarankan mematikan atau "
    "mengabaikan alert; untuk query, beri saran umum (index, penulisan ulang) dan sebut bahwa EXPLAIN belum dijalankan; "
    "bila tidak ada masalah, katakan singkat bahwa semuanya normal."
)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


async def _digests(db: AsyncSession, container: str) -> list[dict]:
    """Pola query terberat dari heartbeat terakhir. Agen sudah mengirimnya tanpa literal; dinormalisasi lagi di sini
    karena data dari agen tidak dipercaya."""
    info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == container)) or {}
    items = []
    for raw in ((info.get("metrics") or {}).get("digests") or [])[:5]:
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            continue
        items.append({
            "database": str(raw.get("database") or "")[:64], "query": normalize_query(raw["text"]),
            "count": _number(raw.get("count")), "seconds": round((_number(raw.get("total_ps")) or 0) / 1e12, 3),
            "rows_examined": _number(raw.get("rows_examined"))})
    return items


async def build_input(db: AsyncSession, report: Report) -> dict:
    data = report.data
    weekly = report.kind == "weekly"
    containers = []
    for section in data.get("containers", []):
        entry = {"name": section["name"],
                 "findings": [{"level": f["level"], "text": f["text"]} for f in section.get("findings", [])],
                 "stats": {k: _number(section.get("stats", {}).get(k)) for k in STAT_KEYS
                           if _number(section.get("stats", {}).get(k)) is not None}}
        if weekly:
            entry["growth"] = [{"database": g["database"], "bytes": _number(g.get("bytes")), "delta": _number(g.get("delta"))}
                               for g in (section.get("stats", {}).get("growth") or [])[:5]]
            entry["queries"] = await _digests(db, section["name"])
        containers.append(entry)
    start = report.period_start.replace(tzinfo=UTC)
    return {"jenis": "harian" if report.kind == "daily" else "mingguan", "periode_mulai": start.date().isoformat(),
            "container_diperiksa": data.get("checked"), "container_bermasalah": containers}


async def summarize(db: AsyncSession, client: httpx.AsyncClient, report_id: int, *, force: bool = False,
                    provider: Provider | None = None) -> AiSummary | None:
    """Membuat ringkasan satu laporan. None bila AI mati, atau ringkasan sudah ada dan `force` tidak diminta. Kegagalan
    penyedia disimpan sebagai baris dengan `error`, supaya dashboard bisa menawarkan Coba lagi."""
    provider = provider or get_provider()
    if provider is None:
        return None
    async with db.begin():
        report = await db.get(Report, report_id)
        if report is None:
            return None
        existing = await db.scalar(select(AiSummary).where(AiSummary.report_id == report_id))
        if existing is not None and not force and not existing.error:
            return existing
        payload = await build_input(db, report)
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    text, error = None, None
    try:
        text = (await provider.complete(SYSTEM, prompt, client))[:MAX_TEXT] or None
        if text is None:
            error = "penyedia menjawab kosong"
    except ProviderError as err:
        error = str(err)[:500]
    except Exception as err:     # apa pun dari penyedia tidak boleh menggagalkan laporan
        logger.exception("ai_provider_crashed")
        error = f"kesalahan tak terduga: {type(err).__name__}"
    async with db.begin():
        row = await db.scalar(select(AiSummary).where(AiSummary.report_id == report_id).with_for_update())
        if row is None:
            row = AiSummary(report_id=report_id, provider=provider.name, model=provider.model, input=payload)
            db.add(row)
        row.provider, row.model, row.input, row.text, row.error = provider.name, provider.model, payload, text, error
        await db.flush()
        await db.refresh(row)
    return row


async def for_report(db: AsyncSession, report_id: int) -> AiSummary | None:
    return await db.scalar(select(AiSummary).where(AiSummary.report_id == report_id))
