"""Tahap 4: normalisasi query, penyedia model yang bisa ditukar, dan ringkasan AI yang tidak mengganggu laporan."""
import json
import re
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.ai import providers
from app.ai import service as ai
from app.ai.models import AiSummary
from app.ai.normalize import normalize_query
from app.containers.models import Heartbeat
from app.core.database import async_session_factory
from app.reports import service as reports
from app.reports.models import Report
from tests.test_dashboard import csrf, post_headers  # noqa: F401
from tests.test_reports import DAY, NAME, NEXT, add_metric, register

# ---------- normalisasi ----------
PII = ["Budi Santoso", "12345678", "1980-01-01", "O'Brien", "081234567890", "rahasia", "3201010101010001", "ab12cd"]

CASES = [
    ("SELECT * FROM pasien WHERE nama = 'Budi Santoso' AND no_rm = 12345678", "SELECT * FROM pasien WHERE nama = ? AND no_rm = ?"),
    ("select * from t1 where kolom_2 = 3", "select * from t1 where kolom_2 = ?"),
    ('SELECT a FROM t WHERE b = "rahasia"', "SELECT a FROM t WHERE b = ?"),
    ("SELECT a FROM t WHERE nama = 'O''Brien'", "SELECT a FROM t WHERE nama = ?"),
    ("SELECT a FROM t WHERE nama = 'O\\'Brien' AND x = 1", "SELECT a FROM t WHERE nama = ? AND x = ?"),
    ("SELECT a FROM t WHERE id IN (1, 2, 3, 4)", "SELECT a FROM t WHERE id IN (?)"),
    ("INSERT INTO t (a, b) VALUES ('x', 1), ('y', 2), ('z', 3)", "INSERT INTO t (a, b) VALUES (?)"),
    ("SELECT * FROM t WHERE tgl = '1980-01-01' AND hp = 081234567890", "SELECT * FROM t WHERE tgl = ? AND hp = ?"),
    ("SELECT * FROM t WHERE x = -5 AND y = 3.14 AND z = 1e10 AND h = 0xFF", "SELECT * FROM t WHERE x = ? AND y = ? AND z = ? AND h = ?"),
    ("SELECT * FROM t WHERE k = x'ab12cd' AND j = b'0101'", "SELECT * FROM t WHERE k = ? AND j = ?"),
    ("SELECT * FROM t WHERE nama LIKE '%Budi%'", "SELECT * FROM t WHERE nama LIKE ?"),
    ("SELECT 1 /* pasien Budi Santoso */ FROM t -- nik 3201010101010001\nWHERE a = 2", "SELECT ? FROM t WHERE a = ?"),
    ("SELECT * FROM `pasien 2` WHERE a=7", "SELECT * FROM `pasien 2` WHERE a=?"),
    ("SELECT * FROM t WHERE a = _utf8'rahasia'", "SELECT * FROM t WHERE a = ?"),
    ("UPDATE t SET a=1,b='x' WHERE id=99", "UPDATE t SET a=?,b=? WHERE id=?"),
]


@pytest.mark.parametrize("sql,expected", CASES)
def test_literals_are_replaced(sql, expected):
    assert normalize_query(sql) == expected


@pytest.mark.parametrize("sql", [c[0] for c in CASES])
def test_no_known_literal_survives(sql):
    out = normalize_query(sql)
    assert not [value for value in PII if value in out]
    assert "'" not in out and '"' not in out


def test_unterminated_string_and_comment_drop_the_rest():
    assert normalize_query("SELECT * FROM t WHERE nama = 'Budi Santoso AND x = 5") == "SELECT * FROM t WHERE nama = ?"
    assert normalize_query("SELECT a FROM t /* Budi") == "SELECT a FROM t"
    assert normalize_query("SELECT a FROM t # nik 3201\nWHERE b = 1") == "SELECT a FROM t WHERE b = ?"
    assert normalize_query(None) == "" and normalize_query("") == ""


def test_result_is_bounded_and_idempotent():
    long = "SELECT " + ", ".join(f"c{i}" for i in range(500)) + " FROM t WHERE a = 'rahasia'"
    out = normalize_query(long)
    assert len(out) <= 300 and normalize_query(out) == out


# ---------- penyedia ----------
def test_provider_is_chosen_by_config_only(monkeypatch):
    for name, cls in (("claude", providers.ClaudeProvider), ("openai", providers.OpenAIProvider), ("ollama", providers.OllamaProvider), ("fake", providers.FakeProvider)):
        monkeypatch.setattr(providers.settings, "AI_PROVIDER", name)
        assert type(providers.get_provider()) is cls
    for off in ("none", "", "NONE", "tidak-ada"):
        monkeypatch.setattr(providers.settings, "AI_PROVIDER", off)
        assert providers.get_provider() is None


async def test_claude_provider_request_and_reply(monkeypatch):
    monkeypatch.setattr(providers.settings, "AI_API_KEY", "kunci-rahasia")
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["headers"], seen["body"] = str(request.url), request.headers, json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "halo "}, {"type": "text", "text": "dunia"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        text = await providers.ClaudeProvider(model="m1").complete("sistem", "isi", http)
    assert text == "halo dunia" and seen["url"].endswith("/v1/messages")
    assert seen["headers"]["x-api-key"] == "kunci-rahasia" and seen["body"]["system"] == "sistem"
    assert seen["body"]["messages"] == [{"role": "user", "content": "isi"}] and seen["body"]["model"] == "m1"


async def test_claude_errors_never_leak_key(monkeypatch):
    monkeypatch.setattr(providers.settings, "AI_API_KEY", "kunci-rahasia")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "kunci-rahasia"}))) as http:
        with pytest.raises(providers.ProviderError) as err:
            await providers.ClaudeProvider(model="m").complete("s", "p", http)
    assert "kunci-rahasia" not in str(err.value) and "401" in str(err.value)
    monkeypatch.setattr(providers.settings, "AI_API_KEY", "")
    async with httpx.AsyncClient() as http:
        with pytest.raises(providers.ProviderError):
            await providers.ClaudeProvider(model="m").complete("s", "p", http)


async def test_openai_provider_request_and_reply(monkeypatch):
    monkeypatch.setattr(providers.settings, "AI_BASE_URL", "https://gateway.test/v1/")
    monkeypatch.setattr(providers.settings, "AI_API_KEY", "kunci-rahasia")
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": " siap "}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await providers.OpenAIProvider(model="m").complete("sistem", "isi", http) == "siap"
    assert seen["url"] == "https://gateway.test/v1/chat/completions" and seen["auth"] == "Bearer kunci-rahasia"
    assert seen["body"]["stream"] is False and seen["body"]["messages"][0] == {"role": "system", "content": "sistem"}
    for bad in (httpx.Response(500), httpx.Response(200, json={"choices": []}), httpx.Response(200, text="bukan json")):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r, b=bad: b)) as http:
            with pytest.raises(providers.ProviderError) as err:
                await providers.OpenAIProvider(model="m").complete("s", "p", http)
        assert "kunci-rahasia" not in str(err.value)


async def test_ollama_provider_request_and_reply():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": " ok lokal "}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await providers.OllamaProvider(model="llama").complete("s", "p", http) == "ok lokal"
    assert seen["url"].endswith("/api/chat") and seen["body"]["stream"] is False and seen["body"]["model"] == "llama"


# ---------- ringkasan ----------
async def make_report(kind="daily", digests=None):
    await register(DAY - timedelta(days=30))
    await add_metric("connections", DAY + timedelta(hours=1), 5, peak=97)
    await add_metric("max_connections", DAY + timedelta(hours=1), 100)
    if digests is not None:
        async with async_session_factory() as db, db.begin():
            db.add(Heartbeat(container=NAME, info={"metrics": {"digests": digests}}))
    async with async_session_factory() as db, db.begin():
        report = Report(kind=kind, period_start=DAY, period_end=NEXT, data=await reports.build(db, kind, DAY, NEXT))
        db.add(report)
        await db.flush()
        return report.id


async def run(report_id, provider=None, **kwargs):
    async with httpx.AsyncClient() as http, async_session_factory() as db:
        return await ai.summarize(db, http, report_id, provider=provider, **kwargs)


async def test_summary_is_stored_with_exact_input_and_provider():
    report_id = await make_report()
    row = await run(report_id, providers.FakeProvider(model="uji"))
    assert row.text.startswith("Ringkasan uji") and row.error is None and (row.provider, row.model) == ("fake", "uji")
    assert row.input["container_bermasalah"][0]["name"] == NAME and row.input["jenis"] == "harian"
    assert row.input["container_bermasalah"][0]["findings"][0]["text"].startswith("Koneksi puncak")
    assert (await run(report_id, providers.FakeProvider(model="lain"))).model == "uji"       # sudah ada: tidak dibuat ulang


async def test_input_never_contains_literals_even_from_untrusted_agent():
    digests = [{"database": "simrs", "text": "SELECT * FROM pasien WHERE nama = 'Budi Santoso' AND no_rm = 12345678 -- 3201010101010001",
                "count": 5, "total_ps": 3_000_000_000_000, "rows_examined": 900},
               {"database": "simrs", "text": 5}, "bukan dict"]
    report_id = await make_report("weekly", digests)
    row = await run(report_id, providers.FakeProvider(model="uji"))
    dumped = json.dumps(row.input, ensure_ascii=False)
    assert not [v for v in PII if v in dumped]
    queries = row.input["container_bermasalah"][0]["queries"]
    assert queries == [{"database": "simrs", "query": "SELECT * FROM pasien WHERE nama = ? AND no_rm = ?", "count": 5,
                        "seconds": 3.0, "rows_examined": 900}]
    assert not re.search(r"'[^']", dumped.replace("'?'", ""))


async def test_failure_is_recorded_not_raised_and_retry_replaces_it():
    report_id = await make_report()
    async with async_session_factory() as db, db.begin():
        report = await db.get(Report, report_id)
        report.data = {**report.data, "containers": [{**report.data["containers"][0], "name": "GAGALKAN"}]}
    row = await run(report_id, providers.FakeProvider(model="uji"))
    assert row.text is None and "dipaksa gagal" in row.error
    async with async_session_factory() as db, db.begin():
        report = await db.get(Report, report_id)
        report.data = {**report.data, "containers": []}
    again = await run(report_id, providers.FakeProvider(model="uji"))          # error → dibuat ulang tanpa force
    assert again.text and again.error is None
    async with async_session_factory() as db:
        assert len((await db.scalars(select(AiSummary))).all()) == 1


async def test_unexpected_provider_crash_is_contained():
    class Broken(providers.Provider):
        name = "rusak"

        async def complete(self, system, prompt, client):
            raise RuntimeError("meledak dengan 'rahasia'")

    row = await run(await make_report(), Broken(model="x"))
    assert row.text is None and row.error == "kesalahan tak terduga: RuntimeError" and "rahasia" not in row.error


async def test_ai_off_creates_nothing(monkeypatch):
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "none")
    assert await run(await make_report()) is None
    async with async_session_factory() as db:
        assert not (await db.scalars(select(AiSummary))).all()


async def test_publish_due_survives_provider_failure_and_still_sends_numbers(monkeypatch):
    await register(DAY - timedelta(days=30))
    await add_metric("connections", DAY + timedelta(hours=1), 5, peak=97)
    await add_metric("max_connections", DAY + timedelta(hours=1), 100)
    monkeypatch.setattr(reports, "due_periods", lambda now: [("daily", DAY, NEXT)])
    monkeypatch.setattr(reports.settings, "TELEGRAM_TOKEN", "tok")
    monkeypatch.setattr(reports.settings, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "claude")
    monkeypatch.setattr(providers.settings, "AI_API_KEY", "k")
    sent = []

    def handler(request: httpx.Request):
        if "anthropic" in str(request.url):
            return httpx.Response(500)
        sent.append(request.content.decode())
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http, async_session_factory() as db:
        published = await reports.publish_due(db, http)
    assert len(published) == 1 and len(sent) == 1 and "Laporan harian" in sent[0]
    assert "Ringkasan" not in sent[0]                                       # AI tidak ikut ke Telegram
    async with async_session_factory() as db:
        row = (await db.scalars(select(AiSummary))).one()
    assert row.text is None and "500" in row.error


async def test_publish_due_stores_summary_when_provider_works(monkeypatch):
    await register(DAY - timedelta(days=30))
    await add_metric("connections", DAY + timedelta(hours=1), 5, peak=97)
    await add_metric("max_connections", DAY + timedelta(hours=1), 100)
    monkeypatch.setattr(reports, "due_periods", lambda now: [("daily", DAY, NEXT)])
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "fake")
    async with httpx.AsyncClient() as http, async_session_factory() as db:
        await reports.publish_due(db, http)
    async with async_session_factory() as db:
        assert (await db.scalars(select(AiSummary))).one().text.startswith("Ringkasan uji")


# ---------- halaman ----------
async def test_report_page_labels_ai_and_offers_retry(client, csrf, monkeypatch):  # noqa: F811
    report_id = await make_report()
    page = await client.get(f"/reports/{report_id}")
    assert "dibuat AI" not in page.text and "Buat sekarang" not in page.text         # AI mati
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "fake")
    assert "Buat sekarang" in (await client.get(f"/reports/{report_id}")).text
    await run(report_id, providers.FakeProvider(model="uji"))
    page = await client.get(f"/reports/{report_id}")
    assert "dibuat AI" in page.text and "Ringkasan uji" in page.text and "tidak mengubah atau menekan alert" in page.text


async def test_regenerate_route(client, csrf, monkeypatch):  # noqa: F811
    report_id = await make_report()
    headers = post_headers(csrf)
    assert (await client.post(f"/reports/{report_id}/ai", headers=headers)).status_code == 409      # AI mati
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "fake")
    assert (await client.post("/reports/999/ai", headers=headers)).status_code == 404
    assert (await client.post(f"/reports/{report_id}/ai", headers={"HX-Request": "true"})).status_code == 403   # tanpa CSRF
    done = await client.post(f"/reports/{report_id}/ai", headers=headers)
    assert done.status_code == 200 and done.headers["hx-refresh"] == "true"
    async with async_session_factory() as db:
        first = (await db.scalars(select(AiSummary))).one()
    again = await client.post(f"/reports/{report_id}/ai", headers=headers)         # force: dibuat ulang, tetap satu baris
    assert again.status_code == 200
    async with async_session_factory() as db:
        assert (await db.scalars(select(AiSummary))).one().id == first.id


# ---------- penyaring log ----------
from app.ai.scrub import scrub_line, scrub_log  # noqa: E402

LOG_CASES = [
    ("ERROR 1062: Duplicate entry 'Budi Santoso' for key 'PRIMARY'", "ERROR 1062: Duplicate entry '?' for key '?'"),
    ('Cannot add row: nama = "O\'Brien" gagal', 'Cannot add row: nama = "?" gagal'),
    ("upload 20261104_pasien_3201010101010001.sql.gz.age gagal HTTP 503", "upload ?_pasien_?.sql.gz.age gagal HTTP 503"),
    ("kirim ke 10.20.30.40 dari pak.budi@rs.example.id", "kirim ke <ip> dari <surel>"),
    ("checksum a3f9c01d22b7e45f8899aabbccdd berbeda", "checksum <hex> berbeda"),
    ("baris 42 dari 1500 file, kode 28", "baris 42 dari 1500 file, kode 28"),
    ("Query gagal: nama = 'Budi tanpa penutup", "Query gagal: nama = '?"),
    ("path /data/pasien/12345678/file", "path /data/pasien/?/file"),
]


@pytest.mark.parametrize("raw,expected", LOG_CASES)
def test_log_values_are_removed(raw, expected):
    assert scrub_line(raw) == expected


def test_log_keeps_last_lines_and_drops_empty_ones():
    lines = [f"baris {i}" for i in range(100)] + ["", "  "]
    out = scrub_log(lines, max_lines=5)
    assert out == [f"baris {i}" for i in range(95, 100)]
    assert scrub_log(None) == [] and scrub_log("a\n\nb") == ["a", "b"] and len(scrub_line("x" * 1000)) == 300


# ---------- penjelasan container ----------
from app.ai.models import AiExplanation  # noqa: E402
from app.alerts.rules import AlertItem  # noqa: E402
from app.backup_control.models import BackupRequest  # noqa: E402

SECRETS = ["Budi Santoso", "3201010101010001", "10.20.30.40", "pak.budi@rs.example.id"]


async def explain_world():
    await register(DAY - timedelta(days=30))
    info = {"disk": {"total": 1000, "free": 50}, "pending": {"count": 3, "bytes": 10},
            "upload_error": {"message": "HTTP 503", "code": 503, "file": "pasien_3201010101010001.gz.age", "attempts": 4},
            "log": ["ERROR 1062: Duplicate entry 'Budi Santoso' for key 'PRIMARY'", "kirim ke 10.20.30.40 gagal"]}
    async with async_session_factory() as db, db.begin():
        db.add(Heartbeat(container=NAME, info=info))
        db.add(BackupRequest(container=NAME, kind="full", requested_by="alice", status="Gagal",
                             note="mariadb-backup gagal untuk pak.budi@rs.example.id",
                             log="baris 1\nDuplicate entry 'Budi Santoso'\n"))
    return [AlertItem("full:siak-db", "Full", "bad", "Full terakhir 3 hari lalu", NAME,
                      detail="log: nama 'Budi Santoso' di 10.20.30.40")]


async def explain(alerts, provider=None, actor="alice"):
    async with httpx.AsyncClient() as http, async_session_factory() as db:
        return await ai.explain_container(db, http, NAME, alerts, actor, provider=provider)


async def test_explanation_input_is_scrubbed_and_stored():
    alerts = await explain_world()
    row = await explain(alerts, providers.FakeProvider(model="uji"))
    dumped = json.dumps(row.input, ensure_ascii=False)
    assert not [v for v in SECRETS if v in dumped]
    data = row.input
    assert data["alert_aktif"][0]["teks"] == "Full terakhir 3 hari lalu" and data["angka"]["disk_bebas"] == 50
    assert data["log_agen"][0] == "ERROR 1062: Duplicate entry '?' for key '?'" and "<ip>" in data["log_agen"][1]
    assert data["permintaan_backup_gagal"][0]["catatan"] == "mariadb-backup gagal untuk <surel>"
    assert data["kesalahan_kirim"]["message"] == "HTTP 503" and "file" not in data["kesalahan_kirim"]
    assert row.text.startswith("Ringkasan uji") and (row.requested_by, row.provider) == ("alice", "fake")


async def test_explanation_failure_is_recorded_and_ai_off_creates_nothing(monkeypatch):
    alerts = await explain_world()
    alerts.append(AlertItem("x", "GAGALKAN", "warn", "GAGALKAN", NAME))
    row = await explain(alerts, providers.FakeProvider(model="uji"))
    assert row.text is None and "dipaksa gagal" in row.error
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "none")
    assert await explain(alerts) is None
    async with async_session_factory() as db:
        assert len((await db.scalars(select(AiExplanation))).all()) == 1
        assert (await ai.latest_explanation(db, NAME)).id == row.id


async def test_explain_route_and_summary_card(client, csrf, monkeypatch):  # noqa: F811
    await explain_world()
    headers = post_headers(csrf)
    page = await client.get(f"/containers/{NAME}")
    assert "Penjelasan AI" not in page.text                                    # AI mati: tidak ada kartu
    assert (await client.post(f"/containers/{NAME}/ai/explain", headers=headers)).status_code == 409
    monkeypatch.setattr(providers.settings, "AI_PROVIDER", "fake")
    assert "Jelaskan masalah" in (await client.get(f"/containers/{NAME}")).text
    assert (await client.post("/containers/tidak-ada/ai/explain", headers=headers)).status_code == 404
    assert (await client.post(f"/containers/{NAME}/ai/explain", headers={"HX-Request": "true"})).status_code == 403
    done = await client.post(f"/containers/{NAME}/ai/explain", headers=headers)
    assert done.status_code == 200 and done.headers["hx-refresh"] == "true"
    page = await client.get(f"/containers/{NAME}")
    assert "dibuat AI" in page.text and "Ringkasan uji" in page.text and "Jelaskan lagi" in page.text
    assert "tidak mengubah atau menekan alert" in page.text
    async with async_session_factory() as db:
        row = await ai.latest_explanation(db, NAME)
    assert not [v for v in SECRETS if v in json.dumps(row.input)]
