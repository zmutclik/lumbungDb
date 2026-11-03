"""Pengumpul metrik di agen: parsing keluaran klien, ukuran harian, dan kegagalan yang tidak menghalangi heartbeat."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "mariadb" / "agent"


@pytest.fixture
def metrics():
    spec = importlib.util.spec_from_file_location("lumbung_metrics_under_test", AGENT_DIR / "metrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATUS = "Threads_connected\t12\nUptime\t500\nSlow_queries\t3\nInnodb_deadlocks\t0\n"
VARIABLES = "max_connections\t151\nperformance_schema\tON\n"
SIZES = "siak\tpasien\t4000\nsiak\tkunjungan\t1000\nbilling\ttagihan\t500\n"
DIGEST = "siak\tSELECT * FROM `pasien` WHERE `id` = ?\t10\t5000\t200\n"


def fake(outputs, calls=None):
    def run(args, **kwargs):
        sql = args[-1]
        if calls is not None:
            calls.append(sql)
        for marker, out in outputs.items():
            if marker in sql:
                return subprocess.CompletedProcess(args, 0, out, "")
        return subprocess.CompletedProcess(args, 1, "", "error")
    return run


OUTPUTS = {"GLOBAL STATUS": STATUS, "GLOBAL VARIABLES": VARIABLES, "information_schema.TABLES": SIZES,
           "events_statements_summary_by_digest": DIGEST}


def test_sample_parses_status_and_limit(metrics):
    sample = metrics.collect_sample(fake(OUTPUTS))
    assert sample["status"]["Threads_connected"] == 12 and sample["max_connections"] == 151
    assert sample["performance_schema"] is True


def test_sample_none_when_user_missing_or_db_down(metrics):
    assert metrics.collect_sample(fake({})) is None
    assert metrics.collect_sample(fake({"GLOBAL STATUS": ""})) is None

    def broken(args, **kwargs):
        raise OSError("tidak ada klien")
    assert metrics.collect_sample(broken) is None


def test_sizes_sum_per_database_and_rank_tables(metrics):
    sizes = metrics.collect_sizes(fake(OUTPUTS))
    assert sizes["databases"] == [{"name": "siak", "bytes": 5000}, {"name": "billing", "bytes": 500}]
    assert sizes["tables"][0] == {"database": "siak", "table": "pasien", "bytes": 4000}


def test_digest_has_no_values_and_is_capped(metrics):
    digests = metrics.collect_digests(fake({"events_statements_summary_by_digest": DIGEST.replace("?", "x" * 500)}))
    assert len(digests[0]["text"]) == metrics.DIGEST_TEXT_LIMIT
    assert metrics.collect_digests(fake({})) is None        # performance_schema nonaktif


def test_sizes_only_once_a_day(metrics):
    now = [1000.0]
    calls = []
    collector = metrics.Collector(lambda: now[0], fake(OUTPUTS, calls))
    first = collector.payload()
    assert "sizes" in first and "digests" in first
    now[0] += 3600
    assert "sizes" not in collector.payload()
    now[0] += metrics.SIZE_INTERVAL_SECONDS
    assert "sizes" in collector.payload()


def test_sizes_retried_when_failed(metrics):
    outputs = {k: v for k, v in OUTPUTS.items() if k != "information_schema.TABLES"}
    collector = metrics.Collector(lambda: 1000.0, fake(outputs))
    assert "sizes" not in collector.payload()
    assert collector.last_sizes_at is None        # dicoba lagi di heartbeat berikutnya
