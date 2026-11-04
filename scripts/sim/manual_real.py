#!/usr/bin/env python3
"""Uji sungguhan Restore manual: LumbungDB asli (uvicorn + MariaDB uji), file backup terenkripsi sungguhan di folder NAS,
dan scripts restore.sh yang dijalankan persis seperti perintah di dashboard, dengan Docker sungguhan.

Dijalankan dari folder server: uv run python ../scripts/sim/manual_real.py IMAGE
IMAGE = zmutclik/mariadb:lumbungdb-<tag> yang sudah dibangun lokal. Butuh container lumbungtest-db (README)."""
import asyncio
LOOP = asyncio.new_event_loop()
import hashlib
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

image = sys.argv[1]
tag = image.split("lumbungdb-")[1]
base = tempfile.mkdtemp(prefix="manual-real-")
os.chmod(base, 0o755)
nas, config_dir, out_dir = (os.path.join(base, d) for d in ("nas", "config", "out"))
for d in (nas, config_dir):
    os.makedirs(d)
SOURCE, SOURCE_C, NEW, NEW_PORT, DB = "siak-db", "manual-real-source", "manual-real-prod", 33181, "lumbung_manual_sim"
problems = []


def sh(cmd, check=True):
    done = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and done.returncode != 0:
        sys.exit(f"gagal: {cmd}\n{done.stdout}\n{done.stderr}")
    return done.stdout


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


keygen = sh(f"docker run --rm --entrypoint age-keygen {image}")
identity = os.path.join(base, "age.key")
with open(identity, "w") as handle:
    handle.write(keygen)
recipient = [line.split()[-1] for line in keygen.splitlines() if "public key" in line][0]
wrong_identity = os.path.join(base, "wrong.key")
with open(wrong_identity, "w") as handle:
    handle.write(sh(f"docker run --rm --entrypoint age-keygen {image}"))

sh(f"docker exec lumbungtest-db mariadb -uroot -proot -e 'DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB}; "
   f"GRANT ALL ON {DB}.* TO app@\"%\"'")
port = free_port()
env = {**os.environ, "DATABASE_URL": f"mysql+asyncmy://app:app@127.0.0.1:33061/{DB}", "DATA_DIR": nas,
       "AGENTS_FILE": os.path.join(config_dir, "agents.conf"), "RUNNERS_FILE": os.path.join(config_dir, "runners.conf"),
       "SECRET_KEY": "sim-" + "x" * 40, "BACKUP_AGE_RECIPIENTS": recipient, "IMAGE_TAG_DEFAULT": tag,
       "COOKIE_SECURE": "false"}
os.environ.update(env)
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

# ---------- sumber: data, full, dua binlog, semua terenkripsi ----------
sh(f"docker rm -f -v {SOURCE_C} {NEW} >/dev/null 2>&1; docker volume rm -f {NEW}-backup >/dev/null 2>&1; true")
sh(f"docker run -d --name {SOURCE_C} -e MARIADB_ROOT_PASSWORD=pw {image} --log-bin=mysql-bin --server-id=1")


def sql(statement):
    database = "" if statement.startswith(("create database", "show")) else "-D appdb"
    return sh(f"docker exec {SOURCE_C} mariadb -uroot -ppw -h127.0.0.1 {database} -BN -e \"{statement}\"").strip()


for _ in range(120):
    ready = subprocess.run(f"docker exec {SOURCE_C} mariadb -uroot -ppw -h127.0.0.1 -e 'select 1'", shell=True,
                           capture_output=True).returncode == 0
    if ready and "init process done" in subprocess.run(f"docker logs {SOURCE_C}", shell=True, capture_output=True,
                                                       text=True).stderr:
        break
    time.sleep(1)
sql("create database appdb; use appdb; create table t(id int primary key, v varchar(20)) engine=innodb; "
    "insert into t select seq, 'awal' from seq_1_to_100")
os.makedirs(os.path.join(nas, SOURCE, "fullbackup"))
os.makedirs(os.path.join(nas, SOURCE, "binlog"))
start_file = sql("show master status").split()[0]
start_no = int(start_file.rsplit(".", 1)[1])
rows = []


def encrypt(cmd, kind, name):
    path = os.path.join(nas, SOURCE, kind, name)
    sh(f"docker exec {SOURCE_C} bash -c \"{cmd} | gzip | age -r {recipient}\" > {path}")
    data = open(path, "rb").read()
    rows.append({"kind": kind, "relpath": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return rows[-1]


full = encrypt("mariadb-backup --backup --user=root --password=pw --host=127.0.0.1 --stream=xbstream "
               "--target-dir=/tmp/bk 2>/dev/null", "fullbackup", "20261104_020000.xbstream.gz.age")
full.update(binlog_start=start_file, binlog_no=None)
sql("insert into appdb.t select seq + 1000, 'a' from seq_1_to_50; flush binary logs")
encrypt(f"cat /var/lib/mysql/mysql-bin.{start_no:06d}", "binlog", f"mysql-bin.{start_no:06d}.gz.age").update(binlog_no=start_no)
sql("insert into appdb.t select seq + 2000, 'b' from seq_1_to_25; update appdb.t set v='ubah' where id <= 10; flush binary logs")
encrypt(f"cat /var/lib/mysql/mysql-bin.{start_no + 1:06d}", "binlog", f"mysql-bin.{start_no + 1:06d}.gz.age").update(binlog_no=start_no + 1)
expected = sql("select count(*), sum(v='ubah') from appdb.t")
sh(f"docker rm -f -v {SOURCE_C}")

# ---------- LumbungDB asli: data, NAS ok, sesi ----------
sys.path.insert(0, os.getcwd())
from datetime import datetime  # noqa: E402

from app.agents.dependencies import registry as agents  # noqa: E402
from app.backups.models import Backup  # noqa: E402
from app.containers.models import Container  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.manual_restore import service  # noqa: E402
from app.manual_restore.models import ManualRestore  # noqa: E402
from app.nas.monitor import ProbeResult  # noqa: E402
from app.nas.service import save_result  # noqa: E402


async def setup():
    agents.add(SOURCE)
    async with async_session_factory() as db:
        await save_result(db, ProbeResult("ok", 10**12, 5 * 10**11))
    async with async_session_factory() as db, db.begin():
        db.add(Container(name=SOURCE))
        await db.flush()
        for number, item in enumerate(rows):
            db.add(Backup(container=SOURCE, kind=item["kind"], relpath=item["relpath"], stored_path=f"{item['kind']}/{item['relpath']}",
                          size=item["size"], sha256=item["sha256"], plain_sha256=secrets.token_hex(32),
                          backup_at=datetime(2026, 11, 4, 2, number), binlog_no=item.get("binlog_no"), chain_id=1,
                          binlog_start=item.get("binlog_start")))
    async with async_session_factory() as db:
        from sqlalchemy import select
        full_id = await db.scalar(select(Backup.id).where(Backup.kind == "fullbackup"))
    async with async_session_factory() as db:
        return await service.create(db, SOURCE, full_id, "all", NEW, NEW_PORT, 4, "sim")


async def session_state():
    async with async_session_factory() as db:
        from sqlalchemy import select
        row = (await db.scalars(select(ManualRestore))).one()
        known = await db.scalar(select(Container.origin).where(Container.name == NEW))
        return row.status, row.note, row.step, known


row, token = LOOP.run_until_complete(setup())
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
                           "--no-access-log"], env=env, stdout=subprocess.DEVNULL, stderr=open(os.path.join(base, "uvicorn.log"), "w"))
url = f"http://127.0.0.1:{port}"
for _ in range(60):
    if subprocess.run(f"curl -fsS {url}/restore.sh >/dev/null", shell=True).returncode == 0:
        break
    time.sleep(1)


def run_script(identity_file):
    cmd = (f"curl -fsSL {url}/restore.sh | LUMBUNG_URL={url} RESTORE_TOKEN={token} AGE_IDENTITY_FILE={identity_file} "
           f"RESTORE_DIR={out_dir} DB_MEM_LIMIT=1g DB_CPUS=1 sh")
    done = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def prod(statement, user="user_backup", password=None):
    pw = password or env_value("BACKUP_PASS")
    return sh(f"docker exec -e MYSQL_PWD={pw} {NEW} mariadb -S /run/mysqld/mysqld.sock -u{user} -BN -e \"{statement}\"").strip()


def env_value(key):
    for line in open(os.path.join(out_dir, ".env")).read().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]


try:
    # 1) kunci salah: gagal dengan pesan jelas, sesi tercatat Gagal, tidak ada container tertinggal
    code, output = run_script(wrong_identity)
    status, note, step, origin = LOOP.run_until_complete(session_state())
    print(f"kunci salah: kode {code} | sesi {status}: {note}")
    if code == 0 or status != "Gagal" or not note:
        problems.append(f"kunci salah seharusnya gagal dan tercatat Gagal: {code} {status} {note}")
    if sh(f"docker ps -aq --filter name={NEW}").strip() or sh("docker ps -aq --filter label=lumbung.restore.tmp=1").strip():
        problems.append("container tertinggal setelah gagal")
    if os.path.exists(os.path.join(out_dir, "dl")):
        sizes = sorted(os.listdir(os.path.join(out_dir, "dl")))
        print("unduhan tersimpan untuk dilanjutkan:", sizes)

    # 2) perintah yang sama dengan kunci benar: unduhan yang ada dipakai, hasil dipulihkan dan didaftarkan
    code, output = run_script(identity)
    print(output[-1500:])
    status, note, step, origin = LOOP.run_until_complete(session_state())
    print(f"kunci benar: kode {code} | sesi {status} {step} | container {origin}")
    if code != 0 or status != "Selesai" or origin != "manual":
        problems.append(f"restore tidak selesai: {code} {status} {note} {origin}")
    else:
        got = prod("select count(*), sum(v='ubah') from appdb.t")
        print("data hasil:", got, "| sumber:", expected)
        if got != expected:
            problems.append("data hasil berbeda dari sumber")
        state = prod("select @@global.read_only, @@global.event_scheduler, "
                     "(select count(*) from mysql.global_priv where user='lumbung_runner')")
        if state != "0\tON\t0":
            problems.append(f"read_only, event scheduler, akun runner: {state!r}")
        prod("insert into appdb.t values (999999, 'tulis')", user="root", password="pw")
        if prod("select v from appdb.t where id=999999") != "tulis":
            problems.append("tidak bisa menulis di hasil restore")
        sh(f"docker exec {NEW} bash -c 'mariadb-backup --backup --user=user_backup --password={env_value('BACKUP_PASS')} "
           "--stream=xbstream --target-dir=/tmp/bk >/dev/null'")
        agents_text = open(env["AGENTS_FILE"]).read()
        token_line = [line for line in agents_text.splitlines() if line.startswith(NEW)]
        if not token_line or token_line[0].split("=")[1].strip() != env_value("LUMBUNG_TOKEN"):
            problems.append("token agen di .env berbeda dari agents.conf")
        if oct(os.stat(os.path.join(out_dir, ".env")).st_mode & 0o777) != "0o600":
            problems.append("izin .env bukan 600")
        if "lumbung/agent.py" not in sh(f"docker exec {NEW} bash -c 'ps -eo args'"):
            problems.append("agen tidak berjalan")
        if sh("docker ps -aq --filter label=lumbung.restore.tmp=1").strip():
            problems.append("container sementara tertinggal")
        if os.path.exists(os.path.join(out_dir, "dl")) or os.path.exists(os.path.join(out_dir, "binlog")):
            problems.append("file unduhan atau binlog tertinggal")
        port_map = sh(f"docker port {NEW}").strip()
        if f":{NEW_PORT}" not in port_map:
            problems.append(f"port salah: {port_map}")
    # 3) token sudah mati
    code, output = run_script(identity)
    print("jalankan lagi setelah selesai:", code, output.strip().splitlines()[-1] if output.strip() else "")
    if code == 0 or "selesai" not in output:
        problems.append("token seharusnya mati setelah pendaftaran")
finally:
    server.send_signal(signal.SIGTERM)
    server.wait(timeout=20)
    sh(f"docker rm -f -v {NEW} {SOURCE_C} >/dev/null 2>&1; docker volume rm -f {NEW}-backup >/dev/null 2>&1; true")
    sh(f"docker run --rm -v {base}:/w --entrypoint bash {image} -c 'rm -rf /w/out' >/dev/null 2>&1; true")
    sh(f"docker exec lumbungtest-db mariadb -uroot -proot -e 'DROP DATABASE IF EXISTS {DB}'")
    shutil.rmtree(base, ignore_errors=True)
print("MASALAH:", problems or "tidak ada")
sys.exit(1 if problems else 0)
