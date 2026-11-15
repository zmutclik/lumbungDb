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
DIGEST_FULL = ("siak\tSELECT * FROM `pasien` p JOIN kunjungan k ON k.pid = p.id WHERE `nama` = ?\t10\t5000\t900000\t10\t7\t3\t2\t1\n")
INDEXES = "siak\tpasien\tPRIMARY\t0\tid\nsiak\tpasien\tidx_nama\t1\tnama,tgl_lahir\n"


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


def test_digest_counters_are_parsed_and_old_format_still_works(metrics):
    full = metrics.collect_digests(fake({"events_statements_summary_by_digest": DIGEST_FULL}))[0]
    assert (full["rows_sent"], full["no_index_used"], full["no_good_index_used"], full["full_join"],
            full["tmp_disk_tables"]) == (10, 7, 3, 2, 1)
    old = metrics.collect_digests(fake({"events_statements_summary_by_digest": DIGEST}))[0]
    assert "rows_sent" not in old and old["rows_examined"] == 200


def test_tables_are_taken_from_digests_by_name_only(metrics):
    digests = [{"database": "siak", "text": "SELECT * FROM `pasien` p JOIN `billing`.`tagihan` t ON t.a = p.a"},
               {"database": "siak", "text": "UPDATE kunjungan SET a = ? WHERE b IN (SELECT x FROM information_schema.tables)"},
               {"database": "", "text": "SELECT 1 FROM tanpa_db"}]
    # tabel sistem dan digest tanpa database dibuang
    assert metrics.referenced_tables(digests) == [("siak", "pasien"), ("billing", "tagihan"), ("siak", "kunjungan")]


def test_tables_are_read_from_real_digest_spacing(metrics):
    # bentuk nyata dari performance_schema: spasi di sekitar titik dan kurung
    digests = [{"database": "simrs", "text": "SELECT COUNT ( * ) FROM `pasien` `p` JOIN `billing` . `tagihan` `t` ON `t` . `a` = `p` . `a`"}]
    assert metrics.referenced_tables(digests) == [("simrs", "pasien"), ("billing", "tagihan")]


def test_indexes_are_collected_for_referenced_tables_only(metrics):
    calls = []
    digests = [{"database": "siak", "text": "SELECT * FROM pasien WHERE nama = ?"}]
    rows = metrics.collect_indexes(digests, fake({"information_schema.STATISTICS": INDEXES}, calls))
    assert rows == [{"database": "siak", "table": "pasien", "index": "PRIMARY", "unique": True, "columns": ["id"]},
                    {"database": "siak", "table": "pasien", "index": "idx_nama", "unique": False,
                     "columns": ["nama", "tgl_lahir"]}]
    assert "table_schema='siak' AND table_name='pasien'" in calls[0]
    assert metrics.collect_indexes([], fake({})) == [] and metrics.collect_indexes(digests, fake({})) is None


def test_table_names_with_quotes_cannot_break_the_statement(metrics):
    calls = []
    metrics.collect_indexes([{"database": "siak", "text": "SELECT * FROM `pa'sien`"}], fake({"STATISTICS": ""}, calls))
    # nama dari \w+ tidak bisa memuat kutip; tetap, kutip di database digest diloloskan
    metrics.collect_indexes([{"database": "si'ak", "text": "SELECT * FROM pasien"}], fake({"STATISTICS": ""}, calls))
    assert "table_schema='si''ak'" in calls[-1]


def test_payload_carries_indexes_with_digests(metrics):
    outputs = {**OUTPUTS, "events_statements_summary_by_digest": DIGEST_FULL, "information_schema.STATISTICS": INDEXES}
    sample = metrics.Collector(lambda: 1000.0, fake(outputs)).payload()
    assert sample["digests"][0]["no_index_used"] == 7 and sample["indexes"][0]["table"] == "pasien"


# ---------- CPU dan memori mariadbd ----------
def fake_proc(root, comm="mariadbd", cgroup_files=None):
    proc, cgroup = root / "proc", root / "cgroup"
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "comm").write_text("custom-entrypoi\n")
    (proc / "self").mkdir()
    (proc / "57").mkdir()
    (proc / "57" / "comm").write_text(comm + "\n")
    # utime=1500 dan stime=500 tick (field 14 dan 15); nama berspasi tidak menggeser field
    rest = ["S"] + ["0"] * 10 + ["1500", "500"] + ["0"] * 30
    (proc / "57" / "stat").write_text(f"57 ({comm} x) " + " ".join(rest) + "\n")
    (proc / "57" / "status").write_text("Name:\tmariadbd\nVmRSS:\t  204800 kB\n")
    (proc / "meminfo").write_text("MemTotal:       8388608 kB\nMemFree:  100 kB\n")
    cgroup.mkdir()
    for name, text in (cgroup_files or {}).items():
        (cgroup / name).write_text(text)
    return str(proc), str(cgroup)


def test_process_reads_cpu_and_rss_of_mariadbd(metrics, tmp_path, monkeypatch):
    monkeypatch.setattr(metrics.os, "sysconf", lambda name: 100)
    proc, cgroup = fake_proc(tmp_path, cgroup_files={"memory.max": "2147483648\n", "cpu.max": "150000 100000\n"})
    sample = metrics.collect_process(proc, cgroup)
    assert sample["cpu_seconds"] == 20.0 and sample["rss_bytes"] == 200 * 1024 * 1024
    assert sample["memory_limit"] == 2 * 1024 ** 3 and sample["cpus"] <= 1.5


def test_process_without_limits_uses_machine_memory(metrics, tmp_path):
    proc, cgroup = fake_proc(tmp_path, cgroup_files={"memory.max": "max\n", "cpu.max": "max 100000\n"})
    sample = metrics.collect_process(proc, cgroup)
    assert sample["memory_limit"] == 8 * 1024 ** 3 and sample["cpus"] >= 1


def test_process_missing_does_not_block_payload(metrics, tmp_path):
    proc, cgroup = fake_proc(tmp_path, comm="bash")
    assert metrics.collect_process(proc, cgroup) is None

    def broken():
        raise RuntimeError("proc rusak")
    payload = metrics.Collector(lambda: 1000.0, fake(OUTPUTS), process=broken).payload()
    assert payload["status"]["Threads_connected"] == 12 and "process" not in payload
    stub = {"cpu_seconds": 1.0, "rss_bytes": 10, "memory_limit": None, "cpus": 2.0}
    assert metrics.Collector(lambda: 1000.0, fake(OUTPUTS), process=lambda: stub).payload()["process"] == stub


# ---------- mode sidecar ----------
def test_sidecar_query_logs_in_over_tcp_with_cnf(metrics, monkeypatch):
    monkeypatch.setenv("LUMBUNG_MODE", "sidecar")
    seen = []

    def run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "1\n", "")

    assert metrics.query("SELECT 1", run) == [["1"]]
    assert f"--defaults-extra-file={metrics.SIDECAR_CNF}" in seen[0] and "--protocol=tcp" in seen[0]
    assert not any(a.startswith("--socket") or a.startswith("--user") for a in seen[0])


def test_sidecar_process_omits_limits_of_its_own_cgroup(metrics, tmp_path, monkeypatch):
    monkeypatch.setenv("LUMBUNG_MODE", "sidecar")
    proc, cgroup = fake_proc(tmp_path, cgroup_files={"memory.max": "2147483648\n", "cpu.max": "150000 100000\n"})
    sample = metrics.collect_process(proc, cgroup)
    assert sample["rss_bytes"] == 200 * 1024 * 1024
    assert sample["memory_limit"] is None and sample["cpus"] is None
