"""Tanya-jawab atas data laporan. Model tidak diberi akses ke database atau SQL: ia hanya membaca satu dokumen JSON yang
disusun di sini dari metrik teragregasi dan metadata (ukuran, jumlah, nama database dan tabel, tanggal). Pertanyaan admin
juga disaring seperti log, karena bisa tak sengaja memuat nama atau nomor."""
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dependencies import registry
from app.ai.models import AiQuestion
from app.ai.providers import Provider, ProviderError, get_provider
from app.ai.scrub import scrub_line
from app.backups.models import Backup
from app.core.database import db_now_plus
from app.reports import ingest, rules
from app.reports import service as report_service
from app.reports.models import Metric, Report

logger = logging.getLogger(__name__)
MAX_QUESTION = 300
MAX_ANSWER = 4000
MAX_CONTEXT = 24000
HISTORY_DAYS = 30
MAX_PER_HOUR = 20          # batas pertanyaan per akun, supaya biaya tidak lepas kendali

SYSTEM = (
    "Kamu menjawab pertanyaan admin database rumah sakit tentang kesehatan dan pertumbuhan database MariaDB yang "
    "dicadangkan. Satu-satunya sumbermu adalah JSON `data` di bawah: ukuran database dan tabel, koneksi puncak, ruang disk, "
    "ukuran full backup, dan temuan laporan terbaru. Jawab dalam bahasa Indonesia, singkat, dengan angka dan satuan dari data "
    "(byte → MB atau GB). Bila data tidak cukup untuk menjawab, katakan apa yang kurang; jangan menebak atau mengarang angka. "
    "Kamu tidak punya akses ke isi data pasien dan jangan memintanya. Jangan menyarankan mematikan atau mengabaikan alert."
)


class QaError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


async def _database_growth(db: AsyncSession, name: str, since, now) -> list[dict]:
    rows = (await db.execute(
        select(Metric.name, Metric.at, Metric.value).where(
            Metric.container == name, Metric.name.like(f"{ingest.SIZE_DB}%"), Metric.at >= since, Metric.at <= now)
        .order_by(Metric.name, Metric.at))).all()
    by_db: dict[str, list] = {}
    for row in rows:
        by_db.setdefault(row.name[len(ingest.SIZE_DB):], []).append((row.at, row.value))
    out = []
    for database, points in by_db.items():
        first, last = points[0], points[-1]
        days = max((last[0] - first[0]).total_seconds() / 86400, 0)
        out.append({"database": database, "bytes_sekarang": int(last[1]), "bytes_awal": int(first[1]),
                    "selisih_bytes": int(last[1] - first[1]), "hari_data": round(days, 1),
                    "awal": first[0].date().isoformat()})
    return sorted(out, key=lambda g: g["selisih_bytes"], reverse=True)[:10]


async def build_context(db: AsyncSession, now) -> dict:
    since = now - timedelta(days=HISTORY_DAYS)
    containers = []
    for name in sorted(registry.entries().active)[:20]:
        entry: dict = {"nama": name}
        entry["database"] = await _database_growth(db, name, since, now)
        tables = (await db.execute(
            select(Metric.name, Metric.value).where(Metric.container == name, Metric.name.like(f"{ingest.SIZE_TABLE}%"),
                                                    Metric.at >= since).order_by(Metric.at.desc(), Metric.value.desc()).limit(40))).all()
        seen = {}
        for row in tables:
            seen.setdefault(row.name[len(ingest.SIZE_TABLE):], int(row.value))
        entry["tabel_terbesar"] = [{"tabel": t, "bytes": b} for t, b in sorted(seen.items(), key=lambda kv: -kv[1])[:5]]
        peaks = (await db.execute(
            select(func.date(Metric.at), func.max(Metric.peak)).where(
                Metric.container == name, Metric.name == "connections", Metric.at >= now - timedelta(days=14))
            .group_by(func.date(Metric.at)).order_by(func.date(Metric.at)))).all()
        entry["koneksi_puncak_harian"] = [{"tanggal": str(d), "puncak": int(p)} for d, p in peaks if p is not None]
        series = (await report_service.disk_series(db, now, [name])).get(name, [])
        if series:
            entry["disk_bebas"] = {"awal": int(series[0][1]), "sekarang": int(series[-1][1]),
                                   "perkiraan_penuh_hari": rules.forecast_full_days(series)}
        fulls = (await db.execute(
            select(Backup.backup_at, Backup.size).where(Backup.container == name, Backup.kind == "fullbackup")
            .order_by(Backup.backup_at.desc()).limit(10))).all()
        entry["full_backup"] = [{"tanggal": at.date().isoformat(), "bytes": size} for at, size in fulls]
        containers.append(entry)
    reports = []
    for report in await db.scalars(select(Report).where(Report.kind == "daily").order_by(Report.period_start.desc()).limit(7)):
        reports.append({"tanggal": report.period_start.date().isoformat(), "diperiksa": report.data.get("checked"),
                        "temuan": [{"container": s["name"], "teks": [f["text"] for f in s["findings"]]}
                                   for s in report.data.get("containers", []) if s.get("findings")]})
    return {"hari_ini": now.date().isoformat(), "riwayat_hari": HISTORY_DAYS, "container": containers,
            "laporan_harian_terbaru": reports}


def _fit(context: dict) -> dict:
    """Konteks dipangkas bila terlalu besar: tabel dulu, lalu riwayat koneksi."""
    for key in ("tabel_terbesar", "koneksi_puncak_harian", "full_backup"):
        if len(json.dumps(context, ensure_ascii=False)) <= MAX_CONTEXT:
            break
        for entry in context["container"]:
            entry.pop(key, None)
    return context


async def ask(db: AsyncSession, client: httpx.AsyncClient, user: str, question: str, *,
              provider: Provider | None = None) -> AiQuestion | None:
    """None bila AI mati. QaError untuk pertanyaan kosong atau terlalu sering. Kegagalan penyedia disimpan."""
    provider = provider or get_provider()
    if provider is None:
        return None
    clean = scrub_line(question)[:MAX_QUESTION]
    if sum(char.isalpha() for char in clean) < 3:
        raise QaError(422, "Tulis pertanyaan yang lebih jelas.")
    async with db.begin():
        recent = await db.scalar(select(func.count()).select_from(AiQuestion).where(
            AiQuestion.asked_by == user, AiQuestion.created_at > db_now_plus(-3600)))
        if (recent or 0) >= MAX_PER_HOUR:
            raise QaError(429, f"Batas {MAX_PER_HOUR} pertanyaan per jam tercapai.")
        context = _fit(await build_context(db, await db.scalar(select(func.now()))))
    payload = {"pertanyaan": clean, "data": context}
    answer, error = None, None
    try:
        answer = (await provider.complete(SYSTEM, json.dumps(payload, ensure_ascii=False), client))[:MAX_ANSWER] or None
        if answer is None:
            error = "penyedia menjawab kosong"
    except ProviderError as err:
        error = str(err)[:500]
    except Exception as err:
        logger.exception("ai_provider_crashed")
        error = f"kesalahan tak terduga: {type(err).__name__}"
    async with db.begin():
        row = AiQuestion(asked_by=user, question=clean, provider=provider.name, model=provider.model, input=payload,
                         answer=answer, error=error)
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


async def history(db: AsyncSession, user: str, limit: int = 10) -> list[AiQuestion]:
    return list(await db.scalars(select(AiQuestion).where(AiQuestion.asked_by == user)
                                 .order_by(AiQuestion.id.desc()).limit(limit)))
