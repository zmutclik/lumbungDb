"""Ringkasan naratif laporan. AI hanya menafsirkan: angka dan temuan sudah dihitung aturan biasa di app.reports, dan
ringkasan ini tidak bisa mengubah atau menekan alert. Bila penyedia gagal, laporan tetap terbit tanpa ringkasan."""
import json
import logging
from datetime import UTC

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AiExplanation, AiSummary
from app.ai.scrub import scrub_line, scrub_log
from app.ai import queries
from app.ai.providers import Provider, ProviderError, get_provider
from app.backup_control.models import BackupRequest
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
    """Pola query terberat dari heartbeat terakhir, dengan sinyal aturan dan index tabelnya (app.ai.queries)."""
    info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == container)) or {}
    return queries.entries(info.get("metrics"))


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


# ---------- penjelasan keadaan satu container ----------
SYSTEM_EXPLAIN = (
    "Kamu membantu admin database rumah sakit memahami masalah pada satu container MariaDB yang dicadangkan. Masukanmu "
    "JSON: alert aktif yang sudah dinilai aturan biasa, baris log agen dan kegagalan terbaru yang nilainya sudah "
    "disamarkan (? atau <ip>), dan beberapa angka. Jelaskan dalam bahasa Indonesia, paling banyak enam butir pendek: apa "
    "yang kemungkinan terjadi, seberapa penting (rendah, sedang, tinggi, mendesak), dan langkah berikutnya yang konkret. "
    "Aturan: hanya pakai data di masukan dan katakan bila bukti kurang; jangan mengarang penyebab sebagai fakta; jangan "
    "pernah menyarankan mematikan atau mengabaikan alert; jangan meminta admin mengirim isi data."
)
SYSTEM_QUERIES = (
    "Kamu membantu DBA rumah sakit menganalisis query lambat MariaDB. Masukanmu JSON berisi pola query terberat yang "
    "nilainya sudah dibuang (diganti ?), sinyal yang dihitung aturan biasa dari penghitung performance_schema (pemindaian "
    "penuh, join tanpa index, tabel sementara di disk, rasio baris diperiksa per baris dikirim), dan index yang sudah ada "
    "pada tabel yang dipakai. Untuk tiap query yang bermasalah tulis dalam bahasa Indonesia: apa yang kemungkinan lambat, "
    "kandidat index konkret (kolom dari WHERE, JOIN, ORDER BY di teks query, urutan kolom dengan alasannya) atau penulisan "
    "ulang, dan risikonya (index memperlambat tulis dan memakai disk). Aturan: EXPLAIN belum dijalankan, jadi semua saran "
    "belum terverifikasi dan harus dikatakan begitu; sarankan mengujinya dulu di database cadangan atau lingkungan uji; jangan "
    "menyarankan index yang sudah ada; jangan mengarang nama kolom atau tabel di luar masukan; bila tidak ada query "
    "bermasalah atau data kurang, katakan singkat; maksimal delapan butir."
)
FAILED_STATUS = ("Gagal", "Tidak selesai", "Ditolak")


async def explain_input(db: AsyncSession, container: str, alerts: list) -> dict:
    """Masukan penjelasan: hanya alert, angka, dan baris log yang sudah disaring."""
    from app.runners.models import Job, RestoreTest
    info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == container)) or {}
    disk = info.get("disk") or {}
    upload_error = info.get("upload_error") or {}
    requests = []
    for row in await db.scalars(select(BackupRequest).where(BackupRequest.container == container, BackupRequest.status.in_(FAILED_STATUS))
                                .order_by(BackupRequest.id.desc()).limit(3)):
        requests.append({"jenis": row.kind, "status": row.status, "catatan": scrub_line(row.note or ""), "log": scrub_log(row.log, 8)})
    tests = []
    for row in await db.scalars(select(RestoreTest).where(RestoreTest.container == container, RestoreTest.status != "Lolos")
                                .order_by(RestoreTest.id.desc()).limit(3)):
        tests.append({"jenis": row.kind, "status": row.status, "langkah": scrub_line(row.failed_step or ""), "log": scrub_log(row.log, 8)})
    jobs = [{"jenis": j.kind, "status": j.status, "catatan": scrub_line(j.note or "")} for j in await db.scalars(
        select(Job).where(Job.container == container, Job.status.in_(("Gagal", "Tidak terlaksana")))
        .order_by(Job.id.desc()).limit(3))]
    return {
        "container": container,
        "alert_aktif": [{"tingkat": a.level, "jenis": a.kind, "teks": scrub_line(a.cond), "rincian": scrub_line(a.detail)}
                        for a in alerts][:10],
        "angka": {"disk_bebas": _number(disk.get("free")), "disk_total": _number(disk.get("total")),
                  "antrean_kirim": _number((info.get("pending") or {}).get("count"))},
        "kesalahan_kirim": {k: scrub_line(str(v)) for k, v in upload_error.items() if k in ("message", "code", "attempts")},
        "log_agen": scrub_log(info.get("log")),
        "permintaan_backup_gagal": requests, "uji_restore_gagal": tests, "tugas_runner_gagal": jobs,
    }


async def explain_container(db: AsyncSession, client: httpx.AsyncClient, container: str, alerts: list, actor: str,
                            *, provider: Provider | None = None) -> AiExplanation | None:
    """None bila AI mati. Kegagalan penyedia disimpan sebagai baris dengan `error`."""
    provider = provider or get_provider()
    if provider is None:
        return None
    async with db.begin():
        payload = await explain_input(db, container, alerts)
    text, error = None, None
    try:
        text = (await provider.complete(SYSTEM_EXPLAIN, json.dumps(payload, ensure_ascii=False, indent=1), client))[:MAX_TEXT] or None
        if text is None:
            error = "penyedia menjawab kosong"
    except ProviderError as err:
        error = str(err)[:500]
    except Exception as err:
        logger.exception("ai_provider_crashed")
        error = f"kesalahan tak terduga: {type(err).__name__}"
    async with db.begin():
        row = AiExplanation(container=container, requested_by=actor, provider=provider.name, model=provider.model,
                            input=payload, text=text, error=error)
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


async def latest_explanation(db: AsyncSession, container: str, kind: str = "keadaan") -> AiExplanation | None:
    return await db.scalar(select(AiExplanation).where(AiExplanation.container == container, AiExplanation.kind == kind)
                           .order_by(AiExplanation.id.desc()).limit(1))


async def analyze_queries(db: AsyncSession, client: httpx.AsyncClient, container: str, actor: str,
                          *, provider: Provider | None = None) -> AiExplanation | None:
    """Analisis slow query atas pola query terberat. None bila AI mati. Tanpa EXPLAIN: lihat app.ai.queries."""
    provider = provider or get_provider()
    if provider is None:
        return None
    async with db.begin():
        info = await db.scalar(select(Heartbeat.info).where(Heartbeat.container == container)) or {}
        metrics = info.get("metrics") or {}
        payload = {"container": container, "performance_schema_aktif": bool(metrics.get("performance_schema")),
                   "query": queries.entries(metrics, limit=10)}
    text, error = None, None
    if not payload["query"]:
        error = ("performance_schema nonaktif atau belum ada data query dari agen"
                 if not payload["performance_schema_aktif"] else "belum ada pola query yang dilaporkan agen")
    else:
        try:
            text = (await provider.complete(SYSTEM_QUERIES, json.dumps(payload, ensure_ascii=False, indent=1), client))[:MAX_TEXT] or None
            if text is None:
                error = "penyedia menjawab kosong"
        except ProviderError as err:
            error = str(err)[:500]
        except Exception as err:
            logger.exception("ai_provider_crashed")
            error = f"kesalahan tak terduga: {type(err).__name__}"
    async with db.begin():
        row = AiExplanation(container=container, requested_by=actor, kind="query", provider=provider.name,
                            model=provider.model, input=payload, text=text, error=error)
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row
