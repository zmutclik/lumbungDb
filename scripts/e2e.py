#!/usr/bin/env python3
"""Uji ujung-ke-ujung tahap 1 dengan container MariaDB sungguhan, sesuai syarat "Selesai bila" di dokumen.
NAS disimulasikan folder lokal. Hanya menyentuh project bernama lumbunge2e*. Dijalankan lewat scripts/e2e.sh."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(os.environ["E2E_WORK"])
AGE_HEADER = b"age-encryption.org/v1\n"
AGE_RECIPIENTS = os.environ["E2E_AGE_RECIPIENTS"]
BASE = os.environ["E2E_URL"]
ADMIN_PASSWORD = os.environ["E2E_ADMIN_PASSWORD"]
TAG = os.environ["E2E_TAG"]
PROJECT = "lumbunge2e"
DB_NAME = "lumbunge2e-baru"
OLD_NAME = "lumbunge2e-lama"
INSTALL_NAME = "lumbunge2e-pasang"
INTERNAL_URL = os.environ["E2E_INTERNAL_URL"]
MIN_FREE_MB = 1500
failures: list[str] = []


def docker_free_mb():
    return shutil.disk_usage("/var/lib/docker" if os.path.exists("/var/lib/docker") else "/").free // (1024 * 1024)


def step(title):
    # Disk Docker biasanya dipakai bersama container lain di mesin ini: uji tidak boleh memenuhinya.
    if docker_free_mb() < MIN_FREE_MB:
        print(f"\nDIHENTIKAN: ruang disk tinggal {docker_free_mb()} MB, batas aman {MIN_FREE_MB} MB")
        sys.exit(3)
    print(f"\n=== {title}", flush=True)


def check(name, condition, detail=""):
    print(f"  {'OK  ' if condition else 'GAGAL'} {name}{'' if condition else f' :: {detail}'}", flush=True)
    if not condition:
        failures.append(name)
    return condition


def run(*args, check_rc=True, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if check_rc and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result


def container_exec(name, *args, user=None):
    command = ["docker", "exec"] + (["-u", user] if user else []) + [name, *args]
    return run(*command, check_rc=False)


def wait_for(description, function, timeout=180, interval=2):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = function()
        if last:
            return last
        time.sleep(interval)
    check(f"menunggu: {description}", False, f"habis {timeout} detik, terakhir: {last!r}")
    return None


class Dashboard:
    def __init__(self):
        self.http = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)
        self.csrf = None

    def login(self):
        page = self.http.get("/login")
        token = page.cookies["login_csrf"]
        response = self.http.post("/login", data={"username": "admin", "password": ADMIN_PASSWORD, "csrf_token": token})
        assert response.status_code == 303, response.text
        match = re.search(r'"X-CSRF-Token": "([0-9a-f]+)"', self.http.get("/dashboard").text)
        self.csrf = match.group(1)

    def write(self, method, path, **kwargs):
        headers = {"X-CSRF-Token": self.csrf, "HX-Request": "true", "Origin": BASE}
        return self.http.request(method, path, headers=headers, **kwargs)

    def status(self):
        return self.http.get("/api/status").json()

    def container(self, name):
        return next((c for c in self.status()["containers"] if c["name"] == name), None)

    def request_rows(self, name):
        return re.findall(r'class="pill pill-\w+">([^<]+)</span>', self.http.get(f"/containers/{name}/backup").text)

    def has_alert(self, key):
        return any(a["key"] == key for a in self.status()["alerts"])

    def backup_page(self, name):
        return self.http.get(f"/containers/{name}/backup").text


def db_query(sql):
    return run("docker", "compose", "-p", PROJECT, "--env-file", str(WORK / ".env"), "exec", "-T", "db", "mariadb",
               "-ulumbung", f"-p{os.environ['E2E_DB_PASSWORD']}", "lumbung", "-BNe", sql, cwd=ROOT).stdout.strip()


def latest_request(name, kind):
    return db_query(f"SELECT id, status FROM backup_requests WHERE container='{name}' AND kind='{kind}' "
                    "ORDER BY id DESC LIMIT 1").split("\t")


def compose_env(path, name, token, port, extra=""):
    path.write_text(
        f"LUMBUNG_URL={INTERNAL_URL}\nLUMBUNG_TOKEN={token}\nLUMBUNG_AGE_RECIPIENTS={AGE_RECIPIENTS}\nIMAGE_TAG={TAG}\nCONTAINER_NAME={name}\n"
        f"CONTAINER_HOSTNAME={name}\nDATA_PORT={port}\nDB_ROOTPASSWORD=rootpass\nDB_APPUSER=appuser\n"
        "DB_APPPASS=apppass\nDB_NAME=appdb\nBACKUP_USER=user_backup\nBACKUP_PASS=backuppass\n"
        "INTERVAL_SECONDS=5\nSTABLE_SECONDS=8\nDB_MEM_LIMIT=1G\n" + extra
    )


def mariadb_compose(env_file, project, *args):
    return run("docker", "compose", "-p", project, "-f", str(ROOT / "mariadb/docker-compose.lumbung.yml"),
               "--env-file", str(env_file), *args)


def sql(name, statement):
    return run("docker", "exec", name, "mariadb", "-uroot", "-prootpass", "appdb", "-BNe", statement).stdout.strip()


def wait_sql_ready(name):
    return wait_for(f"MariaDB {name} siap", lambda: container_exec(
        name, "mariadb", "-uroot", "-prootpass", "-e", "select 1").returncode == 0)


def nas_files(name, kind):
    folder = WORK / "nas" / name / kind
    return sorted(p for p in folder.rglob("*.gz*") if p.is_file()) if folder.exists() else []


def decrypted_sha256(path, key="age-runner.key"):
    """Kunci privat hanya ada di luar LumbungDB; di sini di folder kerja uji."""
    result = run("docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{path}:/berkas:ro", "-v", f"{WORK / 'keys'}:/keys:ro",
                 f"zmutclik/mariadb:lumbungdb-{TAG}", "-c", f"age --decrypt --identity /keys/{key} /berkas | sha256sum", check_rc=False)
    return result.stdout.split()[0] if result.stdout.split() else ""


def request_and_wait(ui, name, what, kind, timeout=240):
    response = ui.write("POST", f"/containers/{name}/requests", data={"what": what})
    assert response.status_code == 200, response.text
    request_id, _ = latest_request(name, kind)

    def finished():
        row = db_query(f"SELECT status FROM backup_requests WHERE id={request_id}")
        return row if row not in ("Menunggu", "Berjalan", "Mengirim") else None

    return wait_for(f"permintaan {what} selesai", finished, timeout=timeout)


def run_install(token, *extra_env):
    folder = WORK / "pasang"
    return subprocess.run(
        ["sh", "-c", f"curl -fsSL {INTERNAL_URL}/install.sh | env INSTALL_DIR={folder} LUMBUNG_URL={INTERNAL_URL} "
                     f"INSTALL_TOKEN={token} {' '.join(extra_env)} sh"],
        capture_output=True, text=True, timeout=420,
    )


def test_install_script(ui):
    """Satu perintah dari halaman Tambah container: script asli di server ini, sampai agen terhubung."""
    step("Tambah container lewat satu perintah")
    busy_port = urlparse(BASE).port   # dipakai nginx uji ini
    response = ui.write("POST", "/containers", data={"name": INSTALL_NAME, "port": str(busy_port)})
    match = re.search(r"INSTALL_TOKEN=(lmi_[\w-]+)", response.text)
    check("perintah tampil", response.status_code == 200 and match, response.text[:300])
    token = match.group(1)
    agent_token = re.search(rf"^{INSTALL_NAME} = (\S+)$", (WORK / "config/agents.conf").read_text(), re.M).group(1)
    env_file = WORK / "pasang/.env"

    refused = run_install(token)
    check("port terpakai ditolak sebelum menulis .env", refused.returncode != 0 and "sudah dipakai" in refused.stderr
          and not env_file.exists(), refused.stdout + refused.stderr)

    other = httpx.post(f"{BASE}/api/install/session", headers={"X-Install-Token": token, "X-Machine-Id": "f" * 32})
    check("mesin lain ditolak", other.status_code == 403, other.text)

    # compose gagal setelah .env dibuat: agen belum terhubung, jadi perintah yang sama harus bisa diulang
    shim = WORK / "shim"
    shim.mkdir()
    real_docker = shutil.which("docker")
    (shim / "docker").write_text(f'#!/bin/sh\ncase "$*" in *" up -d"*) exit 1 ;; esac\nexec {real_docker} "$@"\n')
    (shim / "docker").chmod(0o755)
    broken = run_install(token, "DATA_PORT=33096", f"PATH={shim}:{os.environ['PATH']}")
    check("compose gagal dilaporkan", broken.returncode != 0 and "docker compose gagal" in broken.stderr, broken.stderr)
    before = env_file.read_text()

    first = run_install(token)
    check("dijalankan ulang setelah gagal lalu selesai", first.returncode == 0, first.stdout + first.stderr)
    check("password tidak berubah", env_file.read_text() == before)
    check("container sehat", "healthy" in run("docker", "inspect", "-f", "{{.State.Health.Status}}", INSTALL_NAME).stdout)
    check(".env hanya untuk pemilik", env_file.stat().st_mode & 0o077 == 0)
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line and line[0] != "#")
    check("token agen dan port masuk .env", values["LUMBUNG_TOKEN"] == agent_token and values["DATA_PORT"] == "33096")
    check("password acak terisi", all(len(values[k]) == 32 for k in ("DB_ROOTPASSWORD", "DB_APPPASS", "BACKUP_PASS")))
    check("user acak terisi", re.fullmatch(r"app_[a-z0-9]{12}", values["DB_APPUSER"])
          and re.fullmatch(r"backup_[a-z0-9]{12}", values["BACKUP_USER"]), values["DB_APPUSER"] + " " + values["BACKUP_USER"])
    check("token tidak tampil di keluaran script", token not in first.stdout + first.stderr
          and agent_token not in first.stdout + first.stderr)
    check("password tidak ada di keluaran script", values["DB_ROOTPASSWORD"] not in first.stdout + first.stderr)
    login_sql = run("docker", "exec", INSTALL_NAME, "mariadb", f"-u{values['DB_APPUSER']}", f"-p{values['DB_APPPASS']}",
                    values["DB_NAME"], "-BNe", "SELECT 1", check_rc=False)
    check("user aplikasi bisa masuk", login_sql.stdout.strip() == "1", login_sql.stderr)
    backup_login = run("docker", "exec", INSTALL_NAME, "mariadb", "--defaults-file=/etc/db_backup.cnf", "-BNe",
                       "SELECT CURRENT_USER()", check_rc=False)
    check("user backup acak dipakai script backup", backup_login.stdout.strip() == f"{values['BACKUP_USER']}@localhost",
          backup_login.stdout + backup_login.stderr)

    wait_for("heartbeat dari pemasangan script", lambda: (c := ui.container(INSTALL_NAME)) and c["heartbeat_at"], timeout=120)
    done = run_install(token)
    check("perintah mati setelah agen terhubung", done.returncode != 0 and "sudah terhubung" in done.stderr, done.stderr)
    run("docker", "rm", "-f", INSTALL_NAME, check_rc=False)
    run("docker", "volume", "rm", f"{INSTALL_NAME}_data", f"{INSTALL_NAME}_backup", check_rc=False)
    ui.write("POST", f"/containers/{INSTALL_NAME}/disable")


def main():
    ui = Dashboard()
    step("Akun admin: login, halaman, alert container baru")
    ui.login()
    check("/dashboard terbuka setelah login", ui.http.get("/dashboard").status_code == 200)
    check("/healthz tidak terbuka lewat proxy", ui.http.get("/healthz").status_code == 403)
    check("X-Forwarded-For palsu tidak dipakai", "203.0.113.9" not in fake_ip_probe(ui))

    step("Tambah container baru lewat dashboard")
    response = ui.write("POST", "/containers", data={"name": DB_NAME})
    check("token tampil sekali", response.status_code == 200 and "LUMBUNG_TOKEN=" in response.text, response.text[:300])
    token = re.search(r"LUMBUNG_TOKEN=(\S+)", response.text).group(1)
    check("baris masuk agents.conf", f"{DB_NAME} = {token}" in (WORK / "config/agents.conf").read_text())
    check("token tidak tampil di halaman lain", token not in ui.http.get(f"/containers/{DB_NAME}").text)
    wait_for("alert belum ada heartbeat", lambda: ui.has_alert(f"new:{DB_NAME}"), timeout=30)

    test_install_script(ui)

    step("Pasang lewat compose referensi")
    env_file = WORK / "baru.env"
    compose_env(env_file, DB_NAME, token, 33098)
    mariadb_compose(env_file, DB_NAME, "up", "-d")
    wait_sql_ready(DB_NAME)
    sql(DB_NAME, "CREATE TABLE t (id INT PRIMARY KEY AUTO_INCREMENT, isi VARCHAR(100)); INSERT INTO t (isi) VALUES ('a')")
    state = wait_for("heartbeat pertama", lambda: (c := ui.container(DB_NAME)) and c["heartbeat_at"] and c, timeout=60)
    hostname = Path("/etc/hostname").read_text().strip()
    check("server dibaca dari mesin", state and state["server"] == hostname, state)

    step("Batas keamanan agen di dalam image")
    running_as = container_exec(DB_NAME, "sh", "-c", "ps -eo user,args | grep '[a]gent.py' | awk '{print $1}' | sort -u")
    check("agen berjalan sebagai lumbung", running_as.stdout.strip() == "lumbung", running_as.stdout)
    check("tidak bisa membaca /etc/db_backup.cnf",
          container_exec(DB_NAME, "cat", "/etc/db_backup.cnf", user="lumbung").returncode != 0)
    check("tidak bisa membaca datadir",
          container_exec(DB_NAME, "sh", "-c", "cat /var/lib/mysql/ibdata1 > /dev/null", user="lumbung").returncode != 0)
    # Sebagai lumbung: root di container pun tidak bisa membaca environ user lain. -o memilih proses tertua, yaitu
    # agen, bukan sh milik perintah ini yang mewarisi env docker exec.
    environ = container_exec(DB_NAME, "sh", "-c", "cat /proc/$(pgrep -o -u lumbung -f /opt/lumbung/agent.py)/environ | tr '\\0' '\\n'", user="lumbung")
    check("env agen tanpa password", "LUMBUNG_TOKEN" in environ.stdout and "PASS" not in environ.stdout,
          "nama variabel: " + " ".join(line.split("=")[0] for line in environ.stdout.splitlines()))
    check("sudo hanya untuk helper",
          container_exec(DB_NAME, "sudo", "-n", "/bin/cat", "/etc/shadow", user="lumbung").returncode != 0)
    helper = ["sudo", "-n", "/usr/local/sbin/lumbung-helper"]
    for bad in (["hapus-semua"], ["run-backup", "semua"], ["run-backup", "dump", "a;id"], ["run-backup", "full", "x"],
                ["apply-schedule", "9", "rm -rf /", "off", "off"], ["apply-schedule", "9", "0 2 * * *; id", "off", "off"]):
        check(f"helper menolak {bad}", container_exec(DB_NAME, *helper, *bad, user="lumbung").returncode == 64)
    check("file backup tidak bisa diubah agen",
          container_exec(DB_NAME, "touch", "/backup/fullbackup/x", user="lumbung").returncode != 0)
    check("agen tidak membuka port", "agent.py" not in container_exec(DB_NAME, "sh", "-c", "cat /proc/net/tcp | wc -l").stdout)

    step("Ketiga jenis permintaan berakhir Selesai dengan file di NAS")
    check("Binlog: Selesai", request_and_wait(ui, DB_NAME, "binlog", "binlog") == "Selesai",
          db_query("SELECT note, log FROM backup_requests ORDER BY id DESC LIMIT 1"))
    sql(DB_NAME, "INSERT INTO t (isi) VALUES ('b')")
    check("Dump semua: Selesai", request_and_wait(ui, DB_NAME, "dump", "dump") == "Selesai",
          db_query("SELECT note, log FROM backup_requests ORDER BY id DESC LIMIT 1"))
    check("dump appdb di NAS", bool(nas_files(DB_NAME, "databases/appdb")))
    check("dump _users di NAS", bool(nas_files(DB_NAME, "databases/_users")))
    check("Full: Selesai", request_and_wait(ui, DB_NAME, "full", "full") == "Selesai",
          db_query("SELECT note, log FROM backup_requests ORDER BY id DESC LIMIT 1"))
    fulls = nas_files(DB_NAME, "fullbackup")
    check("full di NAS", bool(fulls))
    source_sum = run("docker", "exec", DB_NAME, "sha256sum", f"/backup/fullbackup/{fulls[-1].name.removesuffix('.age')}").stdout.split()[0]
    check("file di NAS terenkripsi", fulls and fulls[-1].read_bytes()[:len(AGE_HEADER)] == AGE_HEADER)
    check("isi file di NAS cocok dengan sumber", fulls and decrypted_sha256(fulls[-1]) == source_sum)
    check("kunci pemulihan juga bisa membuka", fulls and decrypted_sha256(fulls[-1], "age-pemulihan.key") == source_sum)
    missing_key = run("docker", "exec", "-u", "lumbung", DB_NAME, "sh", "-c", "find / -name 'age-*.key' 2>/dev/null", check_rc=False).stdout.strip()
    check("kunci privat tidak ada di container", not missing_key, missing_key)
    start = db_query(f"SELECT binlog_start FROM backups WHERE container='{DB_NAME}' AND kind='fullbackup' ORDER BY id DESC LIMIT 1")
    check("full mencatat binlog_start", start.startswith("mysql-bin."), start)
    check(".info tidak dikirim sebagai backup", not list((WORK / "nas" / DB_NAME / "fullbackup").glob("*.info")))
    check("file berlabel Manual", "Manual" in ui.backup_page(DB_NAME))
    check("Dump satu database: Selesai", request_and_wait(ui, DB_NAME, "dump:appdb", "dump") == "Selesai")
    check("Dump _users saja: Selesai", request_and_wait(ui, DB_NAME, "dump:_users", "dump") == "Selesai")

    step("Jadwal dari dashboard terlihat di crontab dan bertahan setelah restart")
    response = ui.write("PUT", f"/containers/{DB_NAME}/schedule", data={
        "full_mode": "day", "full_time": "04:07", "dump_mode": "week", "dump_time": "05:09", "dump_dow": "2",
        "binlog_mode": "mins", "binlog_every": "30"})
    check("jadwal disimpan", response.status_code == 200, response.text[:200])
    applied = wait_for("jadwal diterapkan agen", lambda: "7 4 * * *" in container_exec(DB_NAME, "crontab", "-l").stdout, timeout=40)
    check("crontab berisi jadwal baru", bool(applied))
    wait_for("versi dilaporkan", lambda: db_query(
        f"SELECT applied_version FROM backup_schedules WHERE container='{DB_NAME}'") == "1", timeout=30)
    check("dashboard menampilkan laporan agen", "Tiap hari 04:07" in ui.backup_page(DB_NAME))
    run("docker", "restart", DB_NAME)
    wait_sql_ready(DB_NAME)
    tab = container_exec(DB_NAME, "crontab", "-l").stdout
    check("bertahan setelah restart", "7 4 * * *" in tab and "9 5 * * 2" in tab and "*/30 * * * *" in tab, tab)

    step("Jadwal tidak valid ditolak agen, crontab lama tetap")
    db_query(f"UPDATE backup_schedules SET requested_full='rm -rf /', requested_version=requested_version+1 WHERE container='{DB_NAME}'")
    message = wait_for("agen menolak", lambda: db_query(
        f"SELECT error_message FROM backup_schedules WHERE container='{DB_NAME}'").replace("NULL", ""), timeout=40)
    check("error dilaporkan", bool(message), message)
    check("crontab lama tetap", "7 4 * * *" in container_exec(DB_NAME, "crontab", "-l").stdout)
    wait_for("alert jadwal ditolak", lambda: ui.has_alert(f"schedule-error:{DB_NAME}"), timeout=15)
    # Pesan dikirim worker pada putaran berikutnya; jadwal jangan dipulihkan sebelum itu.
    wait_for("pesan jadwal ditolak", lambda: any("Agen menolak jadwal" in m for m in telegram_messages()), timeout=20)
    ui.write("PUT", f"/containers/{DB_NAME}/schedule", data={
        "full_mode": "day", "full_time": "04:07", "dump_mode": "day", "dump_time": "05:09",
        "binlog_mode": "mins", "binlog_every": "30"})
    wait_for("alert hilang setelah jadwal valid", lambda: not ui.has_alert(f"schedule-error:{DB_NAME}"), timeout=40)

    step("Restart saat full berjalan: Tidak selesai, tanpa diulang")
    sql(DB_NAME, "CREATE TABLE besar AS SELECT seq, REPEAT(MD5(seq), 20) isi FROM seq_1_to_200000")
    ui.write("POST", f"/containers/{DB_NAME}/requests", data={"what": "full"})
    request_id, _ = latest_request(DB_NAME, "full")
    wait_for("full berjalan", lambda: db_query(f"SELECT status FROM backup_requests WHERE id={request_id}") == "Berjalan",
             timeout=30, interval=0.3)
    before = len(nas_files(DB_NAME, "fullbackup"))
    run("docker", "restart", "-t", "2", DB_NAME)
    result = wait_for("status Tidak selesai", lambda: db_query(
        f"SELECT status FROM backup_requests WHERE id={request_id}") == "Tidak selesai", timeout=60)
    check("berakhir Tidak selesai", bool(result), db_query(f"SELECT status FROM backup_requests WHERE id={request_id}"))
    wait_sql_ready(DB_NAME)
    socket_check = run("docker", "exec", "-u", "lumbung", DB_NAME, "python3", "-c",
                       "import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/mysqld/mysqld.sock')", check_rc=False)
    check("agen bisa memeriksa socket MariaDB", socket_check.returncode == 0, socket_check.stderr[-200:])
    time.sleep(15)
    check("tidak diulang", len(nas_files(DB_NAME, "fullbackup")) == before)
    leftover = container_exec(DB_NAME, "sh", "-c", "ls /backup/fullbackup/ | grep -c '\\.tmp$'").stdout.strip()
    print(f"  catatan: sisa .tmp full setelah restart: {leftover} (dibersihkan setelah 3 jam)")

    step("Permintaan saat agen mati menjadi Kedaluwarsa dan tidak berjalan")
    run("docker", "stop", "-t", "2", DB_NAME)
    ui.write("POST", f"/containers/{DB_NAME}/requests", data={"what": "binlog"})
    request_id, _ = latest_request(DB_NAME, "binlog")
    expired = wait_for("kedaluwarsa", lambda: db_query(
        f"SELECT status FROM backup_requests WHERE id={request_id}") == "Kedaluwarsa", timeout=150, interval=5)
    check("menjadi Kedaluwarsa", bool(expired))

    step("lumbung-agent --drain saat MariaDB mati")
    volume = f"{DB_NAME}_backup"
    stamp = "20260101_010101"
    run("docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{volume}:/backup", f"zmutclik/mariadb:lumbungdb-{TAG}", "-c",
        f"mkdir -p /backup/databases/darurat && echo isi | gzip > /backup/databases/darurat/darurat-{stamp}.sql.gz")
    drained = run("docker", "run", "--rm", "--entrypoint", "lumbung-agent",
                  "-e", f"LUMBUNG_URL={INTERNAL_URL}", "-e", f"LUMBUNG_TOKEN={token}", "-e", f"LUMBUNG_AGE_RECIPIENTS={AGE_RECIPIENTS}",
                  "-v", f"{volume}:/backup:ro", f"zmutclik/mariadb:lumbungdb-{TAG}", "--drain", check_rc=False)
    check("drain selesai", drained.returncode == 0, drained.stdout + drained.stderr)
    check("file darurat sampai di NAS", bool(nas_files(DB_NAME, "databases/darurat")))

    run("docker", "start", DB_NAME)
    wait_sql_ready(DB_NAME)
    time.sleep(12)
    check("permintaan kedaluwarsa tidak berjalan setelah agen hidup",
          db_query(f"SELECT status FROM backup_requests WHERE id={request_id}") == "Kedaluwarsa")

    step("Backup terjadwal tetap jalan saat LumbungDB dimatikan; binlog belum terkirim ditahan")
    ui.write("PUT", f"/containers/{DB_NAME}/schedule", data={
        "full_mode": "off", "dump_mode": "off", "binlog_mode": "custom", "binlog_cron": "* * * * *"})
    wait_for("binlog tiap menit", lambda: "* * * * * /opt/backup/binlogbackup.sh" in container_exec(DB_NAME, "crontab", "-l").stdout, timeout=40)
    compose_lumbung("stop", "web", "nginx")
    sql(DB_NAME, "INSERT INTO t (isi) VALUES ('saat lumbung mati')")
    count = lambda: int(container_exec(DB_NAME, "sh", "-c", "ls /backup/binlog/*.gz | wc -l").stdout.strip() or 0)
    current = count()
    sql(DB_NAME, "INSERT INTO t (isi) VALUES ('lagi')")
    check("cron membuat arsip baru saat LumbungDB mati", bool(wait_for("arsip binlog baru", lambda: count() > current, timeout=90, interval=5)))

    # Full sukses tidak menghapus arsip yang belum terkirim
    unsent = run("docker", "exec", DB_NAME, "sh", "-c", "ls -t /backup/binlog/*.gz | head -1").stdout.strip()
    unsent_name = os.path.basename(unsent)
    full_run = container_exec(DB_NAME, "/opt/backup/databasebackup.sh")
    check("full tetap berhasil saat LumbungDB mati", full_run.returncode == 0, full_run.stdout[-400:])
    check("arsip belum terkirim ditahan setelah full", container_exec(DB_NAME, "test", "-f", unsent).returncode == 0, unsent)
    check("log mencatat ditahan", "ditahan karena belum terkirim" in full_run.stdout, full_run.stdout[-400:])

    # Melewati batas: dihapus dan dicatat
    container_exec(DB_NAME, "touch", "-d", "4 days ago", unsent)
    run_result = container_exec(DB_NAME, "/opt/backup/binlogbackup.sh")
    check("melewati BINLOG_UNSENT_MAX_DAYS dihapus", container_exec(DB_NAME, "test", "-f", unsent).returncode != 0, run_result.stdout)
    log_tail = container_exec(DB_NAME, "sh", "-c", "cat /backup/log/binlog_log_*.log | tail -20").stdout
    check("penghapusan dicatat PERINGATAN", f"PERINGATAN: arsip {unsent_name}" in log_tail, log_tail[-300:])

    compose_lumbung("start", "web", "nginx")
    wait_for("LumbungDB hidup lagi", lambda: try_status(BASE), timeout=60)
    ui = Dashboard()
    ui.login()
    wait_for("agen mengirim file tertunda", lambda: full_arrived(DB_NAME), timeout=90)
    check("full buatan cron sampai setelah LumbungDB hidup", bool(full_arrived(DB_NAME)))
    wait_for("heartbeat kembali", lambda: not ui.has_alert(f"heartbeat:{DB_NAME}"), timeout=30)

    step("Sudah berjalan: script yang terkunci keluar dengan kode 75")
    locked = container_exec(DB_NAME, "sh", "-c", "exec 9>/run/databasedump.lock; flock 9; /opt/backup/databasedump.sh; echo KODE=$?")
    check("kode keluar 75", "KODE=75" in locked.stdout, locked.stdout + locked.stderr)

    step("NAS tidak ter-mount: alert, upload 503, dan tampilan tetap jalan")
    marker = WORK / "nas" / ".lumbung-mounted"
    marker.unlink()
    wait_for("alert NAS", lambda: ui.has_alert("nas"), timeout=90, interval=3)
    upload = httpx.put(f"{BASE}/api/upload/fullbackup/x.gz.age", content=AGE_HEADER, headers={"X-Token": token})
    check("upload ditolak 503", upload.status_code == 503, upload.status_code)
    check("banner tampil", "NAS tidak ter-mount" in ui.http.get("/dashboard").text)
    refused = ui.write("POST", f"/containers/{DB_NAME}/requests", data={"what": "binlog"})
    check("permintaan ditolak saat NAS bermasalah", refused.status_code == 503, refused.status_code)
    marker.touch()
    wait_for("NAS pulih", lambda: not ui.has_alert("nas"), timeout=90, interval=3)

    step("Container lama: pindah dari image lama, data tetap utuh")
    old_token = re.search(r"LUMBUNG_TOKEN=(\S+)", ui.write("POST", "/containers", data={"name": OLD_NAME}).text).group(1)
    env_old = WORK / "lama.env"
    env_old.write_text(
        f"CONTAINER_NAME={OLD_NAME}\nHOSTNAME=lama-host\nDATA_PORT=33097\nDB_ROOTPASSWORD=rootpass\nDB_APPUSER=appuser\n"
        "DB_APPPASS=apppass\nDB_NAME=appdb\nDNS1=1.1.1.1\nDNS2=8.8.8.8\nBACKUP_USER=user_backup\nBACKUP_PASS=backuppass\nDB_MEM_LIMIT=1G\n")
    run("docker", "compose", "-p", OLD_NAME, "-f", str(WORK / "lama-compose.yml"), "--env-file", str(env_old), "up", "-d")
    wait_sql_ready(OLD_NAME)
    sql(OLD_NAME, "CREATE TABLE lama (id INT PRIMARY KEY); INSERT INTO lama VALUES (1),(2),(3)")
    old_cron = container_exec(OLD_NAME, "crontab", "-l").stdout
    old_host = container_exec(OLD_NAME, "hostname").stdout.strip()
    run("docker", "exec", OLD_NAME, "/opt/backup/databasedump.sh", check_rc=False)
    run("docker", "compose", "-p", OLD_NAME, "-f", str(WORK / "lama-compose.yml"), "--env-file", str(env_old), "down")
    env_new = WORK / "lama-baru.env"
    compose_env(env_new, OLD_NAME, old_token, 33097)
    text = env_new.read_text().replace(f"CONTAINER_HOSTNAME={OLD_NAME}", f"CONTAINER_HOSTNAME={old_host}")
    env_new.write_text(text)
    mariadb_compose(env_new, OLD_NAME, "up", "-d")
    wait_sql_ready(OLD_NAME)
    check("data lama utuh", sql(OLD_NAME, "SELECT COUNT(*) FROM lama") == "3")
    new_cron = container_exec(OLD_NAME, "crontab", "-l").stdout
    first = lambda tab: sorted(line.split("/opt/")[0].strip() for line in tab.splitlines() if "/opt/backup" in line)
    check("jam jadwal bawaan tetap", first(old_cron) == first(new_cron), f"{old_cron} vs {new_cron}")
    wait_for("heartbeat container lama", lambda: (c := ui.container(OLD_NAME)) and c["heartbeat_at"], timeout=60)
    wait_for("dump lama terkirim", lambda: bool(nas_files(OLD_NAME, "databases/appdb")), timeout=90)
    check("dump lama sampai di NAS", bool(nas_files(OLD_NAME, "databases/appdb")))
    groups = ui.http.get("/containers").text
    check("dua container satu server terkelompok", hostname in groups and "3 container" in groups)   # termasuk hasil uji pasang lewat script

    simulate_alerts_and_failures(ui, token, old_token, hostname)
    simulate_files(ui, token)
    simulate_restore(ui)
    simulate_binlog_reset(ui)
    simulate_tls(token)

    step("Nonaktifkan: agen ditolak, riwayat tetap")
    response = ui.write("POST", f"/containers/{OLD_NAME}/disable")
    check("nonaktif", response.status_code == 200)
    check("baris dikomentari", f"# {OLD_NAME} = {old_token}" in (WORK / "config/agents.conf").read_text())
    rejected = httpx.post(f"{BASE}/api/heartbeat", json={}, headers={"X-Token": old_token})
    check("agen ditolak 401", rejected.status_code == 401)
    check("riwayat tetap tampil", "appdb" in ui.backup_page(OLD_NAME))

    print(f"\n{'SEMUA LOLOS' if not failures else 'GAGAL: ' + '; '.join(failures)}")
    return 1 if failures else 0


def telegram_messages():
    path = WORK / "tg" / "pesan.log"
    return [json.loads(line)["text"] for line in path.read_text().splitlines()] if path.exists() else []


def worker_logs():
    return run("docker", "compose", "-p", PROJECT, "--env-file", str(WORK / ".env"), "logs", "--no-log-prefix", "worker", cwd=ROOT).stdout


def simulate_alerts_and_failures(ui, token, old_token, hostname):
    step("Telegram tiruan: alert, pengingat isi, dan pesan pulih")
    messages = telegram_messages()
    check("alert container baru terkirim", any(f"{DB_NAME}: Belum ada heartbeat" in m for m in messages), messages[:5])
    check("alert NAS terkirim", "NAS/NFS tidak ter-mount" in messages, messages[-5:])
    check("pesan pulih NAS terkirim", "Pulih: NAS/NFS tidak ter-mount" in messages, messages[-5:])
    check("jadwal ditolak terkirim tanpa rincian", any("Agen menolak jadwal" in m for m in messages)
          and not any("tidak valid" in m for m in messages))
    check("token Telegram tidak masuk log worker", "e2e-rahasia-telegram" not in worker_logs())

    step("Telegram tidak terjangkau: pengecek tetap jalan, kegagalan masuk log")
    compose_lumbung("stop", "telegram")
    marker = WORK / "nas" / ".lumbung-mounted"
    marker.unlink()
    wait_for("alert NAS saat Telegram mati", lambda: ui.has_alert("nas"), timeout=40, interval=2)
    logged = wait_for("kegagalan tercatat", lambda: "telegram_failed" in worker_logs(), timeout=30)
    check("kegagalan Telegram tercatat di log", bool(logged))
    marker.touch()
    wait_for("NAS pulih", lambda: not ui.has_alert("nas"), timeout=40, interval=2)
    compose_lumbung("start", "telegram")

    step("NAS macet: proses pemeriksa tersangkut, alert dan dashboard tetap jalan")
    hang = WORK / "nas" / ".sim-hang"
    hang.touch()
    started = time.time()
    wait_for("alert NAS tidak merespons", lambda: any(a["key"] == "nas" and "merespons" in a["cond"] for a in ui.status()["alerts"]), timeout=40, interval=1)
    check("terdeteksi dalam ±interval + batas tunggu", time.time() - started < 25, time.time() - started)
    begin = time.time()
    page = ui.http.get("/dashboard")
    check("dashboard tetap menjawab cepat", page.status_code == 200 and time.time() - begin < 2, time.time() - begin)
    check("banner tidak merespons", "NAS tidak merespons" in page.text)
    check("watchdog tetap menjawab", httpx.get(f"{BASE}/api/watchdog").json() == {"nas": "hang"})
    check("upload baru ditolak 503", httpx.put(f"{BASE}/api/upload/fullbackup/x.gz.age", content=AGE_HEADER, headers={"X-Token": token}).status_code == 503)
    time.sleep(12)
    probes = run("docker", "compose", "-p", PROJECT, "--env-file", str(WORK / ".env"), "exec", "-T", "worker", "python", "-c",
                 "import os;print(sum(b'probe.py' in open(f'/proc/{p}/cmdline','rb').read() for p in os.listdir('/proc') if p.isdigit() and int(p) != os.getpid()))", cwd=ROOT).stdout.strip()
    check("tidak ada proses pemeriksa baru selama tersangkut", probes == "1", probes)
    hang.unlink()
    wait_for("pulih saat proses selesai", lambda: not ui.has_alert("nas"), timeout=30, interval=1)
    wait_for("pesan pulih terkirim", lambda: "Pulih: NAS tidak merespons" in telegram_messages(), timeout=20)
    check("pesan pulih NAS macet terkirim", "Pulih: NAS tidak merespons" in telegram_messages())

    step("Pengecek mati: upload berhenti, watchdog memberi tahu")
    compose_lumbung("stop", "worker")
    stale = wait_for("watchdog 503", lambda: httpx.get(f"{BASE}/api/watchdog").status_code == 503, timeout=60, interval=3)
    check("watchdog 503 saat pengecek mati", bool(stale))
    check("upload ditolak saat status basi", httpx.put(f"{BASE}/api/upload/fullbackup/x.gz.age", content=AGE_HEADER, headers={"X-Token": token}).status_code == 503)
    check("banner pengecek", "Pengecek NAS dan alert tidak berjalan" in ui.http.get("/dashboard").text)
    compose_lumbung("start", "worker")
    check("pulih setelah pengecek hidup", bool(wait_for("watchdog 200", lambda: httpx.get(f"{BASE}/api/watchdog").status_code == 200, timeout=40, interval=2)))

    step("Alert umur dump per database, lalu pulih karena backup baru")
    db_query(f"UPDATE backups SET backup_at = NOW() - INTERVAL 40 HOUR WHERE container='{OLD_NAME}' AND database='appdb'")
    key = f"dump:{OLD_NAME}:appdb"
    check("alert dump terlambat", bool(wait_for("alert dump", lambda: ui.has_alert(key), timeout=30)))
    check("pesan tanpa isi data", bool(wait_for("pesan dump", lambda: any(m.startswith(f"{OLD_NAME}: Dump appdb terakhir") for m in telegram_messages()), timeout=20)))
    check("database lain tidak ikut alert", not ui.has_alert(f"dump:{OLD_NAME}:_users"))
    check("Dump: Selesai", request_and_wait(ui, OLD_NAME, "dump:appdb", "dump") == "Selesai")
    check("alert dump pulih", bool(wait_for("pulih", lambda: not ui.has_alert(key), timeout=30)))
    check("pesan pulih dump", bool(wait_for("pesan pulih", lambda: any(m.startswith(f"Pulih: {OLD_NAME}: Dump appdb") for m in telegram_messages()), timeout=20)))

    step("Semua agen di satu server berhenti: satu alert untuk server")
    run("docker", "stop", "-t", "2", DB_NAME, OLD_NAME)
    machine = Path("/etc/machine-id").read_text().strip()
    grouped = wait_for("alert server", lambda: ui.has_alert(f"server:{machine}"), timeout=120, interval=3)
    check("satu alert per server", bool(grouped))
    check("alert per agen tidak ikut", not ui.has_alert(f"heartbeat:{DB_NAME}") and not ui.has_alert(f"heartbeat:{OLD_NAME}"))
    check("menyebut nama server", bool(wait_for("pesan server", lambda: any(f"Agen di server {hostname}" in m for m in telegram_messages()), timeout=20)))
    run("docker", "start", DB_NAME, OLD_NAME)
    wait_sql_ready(DB_NAME)
    wait_sql_ready(OLD_NAME)
    check("alert server pulih", bool(wait_for("pulih", lambda: not ui.has_alert(f"server:{machine}"), timeout=60)))


def simulate_files(ui, token):
    headers = {"X-Token": token}
    step("File belum stabil tidak dikirim; agen dijalankan ulang tidak mengirim ulang")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"stabil-{stamp}.sql.gz"
    run("docker", "exec", DB_NAME, "sh", "-c", f"mkdir -p /backup/databases/stabil && echo isi | gzip > /backup/databases/stabil/{name}")
    time.sleep(3)
    check("belum dikirim sebelum stabil", not (WORK / "nas" / DB_NAME / "databases/stabil" / f"{name}.age").exists())
    check("dikirim setelah stabil", bool(wait_for("file stabil", lambda: (WORK / "nas" / DB_NAME / "databases/stabil" / f"{name}.age").exists(), timeout=40)))
    time.sleep(3)  # file sudah ada di NAS sebelum respons upload-nya tercatat di log
    marker = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    time.sleep(1)  # --since hanya sampai detik; tanpa Z, docker membacanya sebagai waktu lokal
    sent_before = set(json.loads(container_exec(DB_NAME, "cat", "/backup/.lumbung/agent/sent.json").stdout))
    check("file stabil tercatat terkirim", f"databases/stabil/{name}" in sent_before, sorted(sent_before)[:5])
    run("docker", "exec", DB_NAME, "pkill", "-f", "/opt/lumbung/agent.py")
    wait_for("agen hidup lagi", lambda: container_exec(DB_NAME, "pgrep", "-f", "/opt/lumbung/agent.py").returncode == 0, timeout=30)
    time.sleep(20)
    logs = run("docker", "compose", "-p", PROJECT, "--env-file", str(WORK / ".env"), "logs", "--since", marker, "web", cwd=ROOT).stdout
    # File baru, misalnya binlog hasil rotasi saat container start tadi, memang harus tetap terkirim.
    uploads = {path.removesuffix(".age") for path in re.findall(r"PUT /api/upload/(\S+)", logs)}
    resent = uploads & sent_before
    check("tidak ada yang dikirim ulang setelah agen restart", not resent, sorted(resent))
    print(f"  catatan: file baru yang terkirim sesudah agen restart: {sorted(uploads - sent_before)}")

    # 1 GB bila ruang disk cukup; file ini ikut tertulis di volume /backup
    big_mb = 1024 if docker_free_mb() > 5000 else 300
    step(f"Upload file {big_mb} MB lewat proxy")
    big = "20260102_010101.xbstream.gz"
    run("docker", "exec", DB_NAME, "sh", "-c", f"head -c {big_mb * 1024 * 1024} /dev/urandom > /backup/fullbackup/{big}")
    source_sum = run("docker", "exec", DB_NAME, "sha256sum", f"/backup/fullbackup/{big}").stdout.split()[0]
    peak = [0.0]

    def arrived():
        usage = run("docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", f"{PROJECT}-web-1", check_rc=False).stdout.split("/")[0].strip()
        if usage.endswith("MiB"):
            peak[0] = max(peak[0], float(usage[:-3]))
        elif usage.endswith("GiB"):
            peak[0] = max(peak[0], float(usage[:-3]) * 1024)
        return (WORK / "nas" / DB_NAME / "fullbackup" / f"{big}.age").exists()

    check(f"file {big_mb} MB sampai", bool(wait_for("file besar", arrived, timeout=300, interval=2)))
    target = WORK / "nas" / DB_NAME / "fullbackup" / f"{big}.age"
    check("checksum file besar cocok", target.exists() and decrypted_sha256(target) == source_sum)
    check("salinan sementara agen terhapus", container_exec(DB_NAME, "sh", "-c", "ls -A /backup/.lumbung/agent/spool").stdout.strip() == "")
    # Pemakaian cgroup ikut menghitung halaman tmpfs saat NAS tiruan ada di /tmp, jadi yang dinilai RSS puncak proses.
    print(f"  catatan: puncak pemakaian cgroup web {peak[0]:.0f} MiB, termasuk tmpfs dan page cache")
    rss = run("docker", "compose", "-p", PROJECT, "--env-file", str(WORK / ".env"), "exec", "-T", "web", "python", "-c",
              "import glob,re;print(max([int(m.group(1)) for f in glob.glob('/proc/[0-9]*/status') for m in [re.search(r'VmHWM:\\s+(\\d+)', open(f).read())] if m] or [0])//1024)", cwd=ROOT).stdout.strip()
    check(f"RSS puncak proses web tetap kecil ({rss} MiB)", rss.isdigit() and 0 < int(rss) < 400, rss)
    run("docker", "exec", DB_NAME, "rm", "-f", f"/backup/fullbackup/{big}")

    step("Upload terputus di tengah: tidak ada sisa, dan bisa diulang")

    def broken():
        yield AGE_HEADER + b"x" * 5_000_000
        raise ConnectionError("putus")

    try:
        httpx.put(f"{BASE}/api/upload/fullbackup/putus.gz.age", content=broken(), headers=headers, timeout=20)
    except Exception:
        pass
    time.sleep(3)
    folder = WORK / "nas" / DB_NAME / "fullbackup"
    check("tidak ada .part tersisa", not list(folder.glob(".*part")), list(folder.glob(".*part")))
    check("nama akhir tidak dibuat", not (folder / "putus.gz.age").exists())
    retry = httpx.put(f"{BASE}/api/upload/fullbackup/putus.gz.age", content=AGE_HEADER + b"utuh", headers=headers, timeout=20)
    check("upload ulang berhasil", retry.status_code == 200 and (folder / "putus.gz.age").read_bytes() == AGE_HEADER + b"utuh", retry.text)
    db_query(f"DELETE FROM backups WHERE container='{DB_NAME}' AND relpath='putus.gz.age'")

    step("Upload bersamaan melewati batas: 503, dashboard tetap menjawab, dan semua berhasil saat diulang")
    from concurrent.futures import ThreadPoolExecutor

    def slow_body():
        yield AGE_HEADER
        for _ in range(20):
            yield b"y" * 100_000
            time.sleep(0.2)

    def send(index):
        return httpx.put(f"{BASE}/api/upload/fullbackup/bersama{index}.gz.age", content=slow_body(), headers=headers, timeout=60).status_code

    with ThreadPoolExecutor(12) as pool:
        futures = [pool.submit(send, index) for index in range(12)]
        time.sleep(2)
        begin = time.time()
        check("dashboard menjawab saat upload penuh", ui.http.get("/dashboard").status_code == 200 and time.time() - begin < 3)
        codes = [future.result() for future in futures]
    check("sebagian ditolak 503", 503 in codes and 200 in codes and set(codes) <= {200, 503}, codes)
    with ThreadPoolExecutor(2) as pool:
        retried = list(pool.map(lambda i: httpx.put(f"{BASE}/api/upload/fullbackup/bersama{i}.gz.age", content=AGE_HEADER + b"z" * 100, headers=headers, timeout=60).status_code,
                                [i for i, code in enumerate(codes) if code == 503]))
    check("semua berhasil saat diulang", set(retried) <= {200}, retried)
    db_query(f"DELETE FROM backups WHERE container='{DB_NAME}' AND relpath LIKE 'bersama%'")


def restore_check(*args):
    return run("docker", "run", "--rm", "--entrypoint", "/sim/restore_check.sh",
               "-v", f"{WORK / 'nas' / DB_NAME}:/nas:ro", "-v", f"{ROOT / 'scripts/sim'}:/sim:ro", "-v", f"{WORK / 'keys'}:/keys:ro",
               f"zmutclik/mariadb:lumbungdb-{TAG}", *args, check_rc=False)


def simulate_restore(ui):
    step("Pemulihan sungguhan dari file di NAS: full + binlog, dan dump")
    sql(DB_NAME, "DROP TABLE IF EXISTS besar")
    check("Full: Selesai", request_and_wait(ui, DB_NAME, "full", "full") == "Selesai")
    in_full = int(sql(DB_NAME, "SELECT COUNT(*) FROM t"))
    sql(DB_NAME, "INSERT INTO t (isi) VALUES ('setelah full 1'), ('setelah full 2')")
    source = int(sql(DB_NAME, "SELECT COUNT(*) FROM t"))
    check("Binlog: Selesai", request_and_wait(ui, DB_NAME, "binlog", "binlog") == "Selesai")

    def latest_full():
        return db_query(f"SELECT stored_path, binlog_start, chain_id FROM backups WHERE container='{DB_NAME}' AND kind='fullbackup' "
                        "AND binlog_start IS NOT NULL ORDER BY backup_at DESC LIMIT 1").split("\t")

    # Full masuk rantai saat binlog awalnya tiba. Binlog itu bisa berasal dari cron dan masih menunggu STABLE_SECONDS.
    linked = wait_for("full terkait", lambda: latest_full()[2] not in ("NULL", ""), timeout=45)
    row = latest_full()
    check("full terkait ke rantai binlog", bool(linked), row)
    time.sleep(12)  # arsip cron lain yang masih menunggu STABLE_SECONDS
    number = int(row[1].rsplit(".", 1)[1])
    binlogs = db_query(f"SELECT stored_path FROM backups WHERE container='{DB_NAME}' AND kind='binlog' AND chain_id={row[2]} "
                       f"AND binlog_no>={number} ORDER BY binlog_no").split("\n")
    print(f"  catatan: {row[0]} + {len(binlogs)} binlog dari {row[1]}")
    result = restore_check("full", row[0], *binlogs)
    check("full dan binlog pulih", result.returncode == 0, result.stdout[-400:] + result.stderr[-400:])
    check(f"isi full cocok ({in_full} baris)", f"isi_full: {in_full}" in result.stdout, result.stdout[-300:])
    check(f"binlog membawa data terbaru ({source} baris)", f"isi_binlog: {source}" in result.stdout, result.stdout[-300:])

    check("Dump: Selesai", request_and_wait(ui, DB_NAME, "dump:appdb", "dump") == "Selesai")
    dump = db_query(f"SELECT stored_path FROM backups WHERE container='{DB_NAME}' AND database='appdb' ORDER BY backup_at DESC LIMIT 1")
    result = restore_check("dump", dump)
    check(f"dump pulih ({source} baris)", f"isi_dump: {source}" in result.stdout, result.stdout[-300:] + result.stderr[-300:])


def simulate_binlog_reset(ui):
    step("Nomor binlog kembali kecil (seperti setelah restore): rantai baru, bukan temuan integritas")
    before = int(db_query(f"SELECT MAX(chain_id) FROM backups WHERE container='{DB_NAME}' AND kind='binlog'"))
    events = db_query(f"SELECT COUNT(*) FROM integrity_events WHERE container='{DB_NAME}'")
    run("docker", "exec", DB_NAME, "mariadb", "-uroot", "-prootpass", "-e", "RESET MASTER")
    sql(DB_NAME, "INSERT INTO t (isi) VALUES ('rantai baru')")
    check("Binlog: Selesai", request_and_wait(ui, DB_NAME, "binlog", "binlog") == "Selesai",
          db_query("SELECT note, log FROM backup_requests ORDER BY id DESC LIMIT 1"))
    after = int(db_query(f"SELECT MAX(chain_id) FROM backups WHERE container='{DB_NAME}' AND kind='binlog'"))
    check("rantai baru terhitung", after == before + 1, (before, after))
    versions = db_query(f"SELECT COUNT(*) FROM backups WHERE container='{DB_NAME}' AND stored_path LIKE 'binlog/%.v%'")
    check("nama sama isi beda disimpan sebagai versi", int(versions) >= 1, versions)
    check("bukan temuan integritas", db_query(f"SELECT COUNT(*) FROM integrity_events WHERE container='{DB_NAME}'") == events)


def simulate_tls(token):
    step("HTTPS lewat nginx-tls.conf: sertifikat dipercaya dan tidak dipercaya")
    certs = WORK / "certs"
    certs.mkdir(exist_ok=True)
    ip = run("hostname", "-I").stdout.split()[0]
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=lumbung-e2e",
        "-addext", f"subjectAltName=IP:{ip}", "-keyout", str(certs / "privkey.pem"), "-out", str(certs / "fullchain.pem"))
    (certs / "privkey.pem").chmod(0o644)
    run("docker", "run", "-d", "--name", "lumbunge2e-tls", "--network", f"{PROJECT}_default", "--ip", "172.31.77.200",
        "-p", "18444:443", "-p", "18081:80",
        "-v", f"{ROOT}/deploy/nginx-tls.conf:/etc/nginx/conf.d/default.conf:ro",
        "-v", f"{ROOT}/deploy/http-common.inc:/etc/nginx/lumbung/http-common.inc:ro",
        "-v", f"{ROOT}/deploy/locations.inc:/etc/nginx/lumbung/locations.inc:ro",
        "-v", f"{ROOT}/server/app/static:/srv/static:ro", "-v", f"{certs}:/etc/nginx/certs:ro", "nginx:1.27-alpine")
    base = f"https://{ip}:18444"
    ready = wait_for("nginx TLS", lambda: tls_up(base, certs), timeout=30)
    check("nginx-tls.conf berjalan", bool(ready), run("docker", "logs", "lumbunge2e-tls", check_rc=False).stderr[-300:])
    client = httpx.Client(base_url=base, verify=str(certs / "fullchain.pem"), timeout=20)
    page = client.get("/login")
    check("HSTS terpasang", "max-age" in page.headers.get("strict-transport-security", ""))
    check("HTTP dialihkan ke HTTPS", httpx.get(f"http://{ip}:18081/login").status_code == 301)
    check("watchdog tetap terbuka lewat HTTP", httpx.get(f"http://{ip}:18081/api/watchdog").status_code == 200)
    response = client.post("/login", data={"username": "admin", "password": ADMIN_PASSWORD, "csrf_token": page.cookies["login_csrf"]},
                           headers={"Origin": base}, cookies={"login_csrf": page.cookies["login_csrf"]})
    check("login lewat HTTPS", response.status_code == 303, response.status_code)
    wrong = client.post("/login", data={"username": "admin", "password": ADMIN_PASSWORD, "csrf_token": "x"},
                        headers={"Origin": base}, cookies={"login_csrf": "x"})
    other = client.post("/login", data={"username": "admin", "password": "x", "csrf_token": "x"},
                        headers={"Origin": f"http://{ip}:18444"}, cookies={"login_csrf": "x"})
    check("skema Origin mengikuti proxy tepercaya", wrong.status_code != 403 and other.status_code == 403, (wrong.status_code, other.status_code))

    volume = f"{DB_NAME}_backup"
    name = f"tls-{time.strftime('%Y%m%d_%H%M%S')}.sql.gz"
    run("docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{volume}:/backup", f"zmutclik/mariadb:lumbungdb-{TAG}", "-c",
        f"mkdir -p /backup/databases/tls && echo isi | gzip > /backup/databases/tls/{name}")
    run("docker", "stop", "-t", "2", DB_NAME)
    untrusted = run("docker", "run", "--rm", "--entrypoint", "lumbung-agent", "-e", f"LUMBUNG_URL={base}", "-e", f"LUMBUNG_TOKEN={token}", "-e", f"LUMBUNG_AGE_RECIPIENTS={AGE_RECIPIENTS}",
                    "-v", f"{volume}:/backup:ro", f"zmutclik/mariadb:lumbungdb-{TAG}", "--drain", check_rc=False)
    check("agen menolak sertifikat tak dipercaya", untrusted.returncode != 0 and "LUMBUNG_AGE_RECIPIENTS" not in untrusted.stdout + untrusted.stderr and not (WORK / "nas" / DB_NAME / "databases/tls" / f"{name}.age").exists(), untrusted.stdout[-200:])
    trusted = run("docker", "run", "--rm", "--entrypoint", "lumbung-agent", "-e", f"LUMBUNG_URL={base}", "-e", f"LUMBUNG_TOKEN={token}", "-e", f"LUMBUNG_AGE_RECIPIENTS={AGE_RECIPIENTS}",
                  "-e", "SSL_CERT_FILE=/certs/fullchain.pem", "-v", f"{certs}:/certs:ro",
                  "-v", f"{volume}:/backup:ro", f"zmutclik/mariadb:lumbungdb-{TAG}", "--drain", check_rc=False)
    check("agen mengirim lewat HTTPS tepercaya", trusted.returncode == 0 and (WORK / "nas" / DB_NAME / "databases/tls" / f"{name}.age").exists(), trusted.stdout[-300:] + trusted.stderr[-200:])
    run("docker", "start", DB_NAME)
    wait_sql_ready(DB_NAME)
    run("docker", "rm", "-f", "lumbunge2e-tls")


def tls_up(base, certs):
    try:
        return httpx.get(f"{base}/login", verify=str(certs / "fullchain.pem"), timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def full_arrived(name):
    return db_query(f"SELECT COUNT(*) FROM backups WHERE container='{name}' AND kind='fullbackup' AND request_id IS NULL") != "0"


def try_status(base):
    try:
        return httpx.get(f"{base}/login", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


def fake_ip_probe(ui):
    """Login gagal dengan X-Forwarded-For palsu: yang tercatat IP sebenarnya."""
    page = ui.http.get("/login")
    httpx.post(f"{BASE}/login", data={"username": "admin", "password": "salah-sekali-sekali", "csrf_token": page.cookies["login_csrf"]},
               cookies={"login_csrf": page.cookies["login_csrf"]}, headers={"X-Forwarded-For": "203.0.113.9"})
    return db_query("SELECT GROUP_CONCAT(ip) FROM login_failures")


def compose_lumbung(*args):
    return run("docker", "compose", "-p", PROJECT, "--env-file", str(WORK / ".env"), *args, cwd=ROOT)  # COMPOSE_FILE dari e2e.sh


if __name__ == "__main__":
    sys.exit(main())
