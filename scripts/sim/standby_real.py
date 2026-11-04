#!/usr/bin/env python3
"""Uji sungguhan database cadangan terhadap Docker: full + dua binlog dipulihkan (standby_full), lalu satu binlog baru
diterapkan (standby_apply). Isi tabel di database cadangan dibandingkan dengan sumber. Pemakaian: standby_real.py IMAGE"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runner"))
import runner as r  # noqa: E402

image = sys.argv[1]
base = tempfile.mkdtemp(prefix="standby-real-")
os.chmod(base, 0o755)
nas, scratch = os.path.join(base, "nas"), os.path.join(base, "scratch")
os.makedirs(nas)
os.makedirs(scratch)
CONTAINER, SOURCE, PORT = "siak-db", "standby-real-source", 33170


def sh(cmd):
    done = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"gagal: {cmd}\n{done.stdout}\n{done.stderr}")
    return done.stdout


def sql(statement):
    database = "" if statement.startswith("create database") or statement.startswith("show") else "-D appdb"
    return sh(f"docker exec {SOURCE} mariadb -uroot -ppw -h127.0.0.1 {database} -BN -e \"{statement}\"").strip()


keygen = sh(f"docker run --rm --entrypoint age-keygen {image}")
identity = os.path.join(base, "age.key")
with open(identity, "w") as handle:
    handle.write(keygen)
recipient = [line.split()[-1] for line in keygen.splitlines() if "public key" in line][0]

sh(f"docker rm -f {SOURCE} {r.standby_container(CONTAINER)} >/dev/null 2>&1 || true")
sh(f"docker run -d --name {SOURCE} -e MARIADB_ROOT_PASSWORD=pw {image} --log-bin=mysql-bin --server-id=1")
files, ids = [], iter(range(1, 100))
try:
    for _ in range(120):
        ready = subprocess.run(f"docker exec {SOURCE} mariadb -uroot -ppw -h127.0.0.1 -e 'select 1'", shell=True,
                               capture_output=True).returncode == 0
        if ready and "init process done" in subprocess.run(f"docker logs {SOURCE}", shell=True, capture_output=True,
                                                           text=True).stderr:
            break
        time.sleep(1)
    sql("create database appdb; use appdb; create table t(id int primary key, v varchar(20)) engine=innodb; "
        "insert into t select seq, 'awal' from seq_1_to_100")

    def add(kind, path, **extra):
        data = open(path, "rb").read()
        entry = {"id": next(ids), "kind": kind, "relpath": os.path.basename(path), "size": len(data),
                 "sha256": hashlib.sha256(data).hexdigest(), "database": None, "binlog_no": None, "binlog_start": None,
                 "chain_id": 1, **extra}
        files.append(entry)
        return entry

    def encrypt(cmd, name):
        sh(f"docker exec {SOURCE} bash -c \"{cmd} | gzip | age -r {recipient}\" > {nas}/{name}")
        return os.path.join(nas, name)

    def current_binlog():
        return sql("show master status").split()[0]

    start_file = current_binlog()
    start_no = int(start_file.rsplit(".", 1)[1])
    full = add("fullbackup", encrypt("mariadb-backup --backup --user=root --password=pw --host=127.0.0.1 "
                                     "--stream=xbstream --target-dir=/tmp/bk 2>/dev/null", "full.xbstream.gz.age"),
               binlog_start=start_file)

    def archive(number):
        name = f"mysql-bin.{number:06d}"
        return add("binlog", encrypt(f"cat /var/lib/mysql/{name}", f"{name}.gz.age"), binlog_no=number)

    sql("insert into appdb.t select seq + 1000, 'a' from seq_1_to_50; flush binary logs")
    archive(start_no)
    sql("insert into appdb.t select seq + 2000, 'b' from seq_1_to_25; update appdb.t set v='ubah' where id <= 10; "
        "flush binary logs")
    archive(start_no + 1)
    expected_first = sql("select count(*), sum(v='ubah') from appdb.t")
    later = None
    sql("insert into appdb.t select seq + 3000, 'c' from seq_1_to_7; flush binary logs")
    later = archive(start_no + 2)
    expected_final = sql("select count(*), sum(v='ubah') from appdb.t")
finally:
    sh(f"docker rm -f {SOURCE}")


class Local:
    def download(self, job_id, entry, dest):
        shutil.copy(os.path.join(nas, entry["relpath"]), dest)


config = {"scratch_dir": scratch, "age_identity": identity, "image": image, "ready_seconds": 180,
          "standby_port_base": PORT, "standby_bind": "127.0.0.1"}
standby_name = r.standby_container(CONTAINER)


def run_job(kind, entries, extra=None):
    job = {"id": 20 + len(kind), "kind": kind, "container": CONTAINER, "files": entries, **({"standby": extra} if extra else {})}
    worker = r.StandbyRunner(config, Local(), job, progress=lambda step: print("   ", step, flush=True))
    status, _ = worker.execute()
    print(f"{kind}: {status} {worker.note or ''} {worker.extra}")
    return status, worker.extra


def read_standby(statement):
    password = open(os.path.join(scratch, "standby", CONTAINER, "current.pw")).read().strip()
    return sh(f"docker exec -e MYSQL_PWD={password} {standby_name} mariadb -S /run/mysqld/mysqld.sock -ulumbung_runner "
              f"-BN -e \"{statement}\"").strip()


problems = []
status, extra = run_job("standby_full", [full] + [f for f in files if f["kind"] == "binlog" and f["binlog_no"] <= start_no + 1])
if status != "Lolos":
    problems.append("standby_full tidak lolos")
else:
    got = read_standby("select count(*), sum(v='ubah') from appdb.t")
    print("setelah full+binlog:", got, "| sumber:", expected_first)
    if got != expected_first:
        problems.append("isi setelah full+binlog berbeda dari sumber")
    flags = read_standby("select @@global.read_only, @@global.event_scheduler")
    print("read_only dan event_scheduler:", repr(flags))
    if flags not in ("1\tOFF", "ON\tOFF"):
        problems.append(f"database cadangan tidak read_only atau event scheduler hidup: {flags!r}")
    if sh(f"docker port {standby_name}").strip().split()[-1] != f"127.0.0.1:{PORT}":
        problems.append("port tidak terpasang ke 127.0.0.1")
    applied = extra["standby"]["applied_binlog_no"]
    status, extra2 = run_job("standby_apply", [later], {"applied_binlog_no": applied, "port": PORT})
    if status != "Lolos":
        problems.append("standby_apply tidak lolos")
    else:
        got = read_standby("select count(*), sum(v='ubah') from appdb.t")
        print("setelah apply:", got, "| sumber:", expected_final)
        if got != expected_final:
            problems.append("isi setelah apply berbeda dari sumber")
    # pemulihan full kedua menggantikan yang lama tanpa meninggalkan sisa
    status, _ = run_job("standby_full", [full] + [f for f in files if f["kind"] == "binlog"], {"port": PORT})
    if status != "Lolos" or read_standby("select count(*) from appdb.t") != expected_final.split()[0]:
        problems.append("pemulihan ulang tidak menghasilkan data yang sama")
    left = sorted(set(os.listdir(os.path.join(scratch, "standby", CONTAINER))))
    print("isi folder standby:", left)
    if any(x in left for x in ("new", "old", "binlog", "apply.sql", "applying")):
        problems.append(f"sisa pekerjaan: {left}")
    pw_mode = oct(os.stat(os.path.join(scratch, "standby", CONTAINER, "current.pw")).st_mode & 0o777)
    if pw_mode != "0o600":
        problems.append(f"izin password {pw_mode}")
leftover = sh("docker ps -aq --filter label=lumbung.runner=1").split()
if leftover:
    problems.append("container sementara tersisa")
# Penghapusan lewat tugas standby_remove: container, datadir, password, dan catatan port hilang.
status, _ = run_job("standby_remove", [])
alive = sh(f"docker ps -aq --filter name={standby_name}").split()
folder = os.path.join(scratch, "standby", CONTAINER)
remaining = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
print("setelah hapus: container", alive, "| isi folder", remaining)
if status != "Lolos" or alive or remaining:
    problems.append(f"penghapusan tidak bersih: {status} {alive} {remaining}")

# ---------- Naikkan ke production: database cadangan baru, lalu dinaikkan menjadi container bernama baru ----------
NEW, NEW_PORT = "standby-real-prod", 33171
promote_dir = os.path.join(scratch, "promoted")
config["promote_dir"], config["promote_memory"], config["promote_cpus"] = promote_dir, "1g", "1"
sh(f"docker rm -f -v {NEW} >/dev/null 2>&1 || true; docker volume rm -f {NEW}-backup >/dev/null 2>&1 || true")
status, extra = run_job("standby_full", [full] + [f for f in files if f["kind"] == "binlog"], {"port": PORT})
if status != "Lolos":
    problems.append("full untuk uji penaikan tidak lolos")
else:
    class FakeClient(Local):
        base_url = "https://lumbung.example"

    job = {"id": 77, "kind": "standby_promote", "container": CONTAINER, "files": [],
           "standby": {"applied_binlog_no": extra["standby"]["applied_binlog_no"], "port": PORT,
                       "promote": {"name": NEW, "port": NEW_PORT, "token": "tok-promote-test", "age_recipients": recipient}}}
    worker = r.StandbyRunner(config, FakeClient(), job, progress=lambda step: print("   ", step, flush=True))
    status, _ = worker.execute()
    print(f"standby_promote: {status} {worker.note or ''}")
    if status != "Lolos":
        problems.append(f"penaikan tidak lolos: {worker.note}")
    else:
        env = {k: v for k, _, v in (l.partition("=") for l in open(os.path.join(promote_dir, NEW, ".env")).read().splitlines())}
        if oct(os.stat(os.path.join(promote_dir, NEW, ".env")).st_mode & 0o777) != "0o600":
            problems.append("izin .env bukan 600")

        def prod(statement, user="user_backup", password=None):
            pw = password or env["BACKUP_PASS"]
            return sh(f"docker exec -e MYSQL_PWD={pw} {NEW} mariadb -S /run/mysqld/mysqld.sock -u{user} -BN -e \"{statement}\"").strip()

        print("data di production:", prod("select count(*), sum(v='ubah') from appdb.t"), "| sumber:", expected_final)
        if prod("select count(*), sum(v='ubah') from appdb.t") != expected_final:
            problems.append("data production berbeda dari sumber")
        state = prod("select @@global.read_only, @@global.event_scheduler")
        print("read_only, event_scheduler:", repr(state))
        if state != "0\tON":
            problems.append(f"production tidak siap tulis atau event scheduler mati: {state!r}")
        accounts = prod("select user from mysql.global_priv where user in ('lumbung_runner')")
        if accounts:
            problems.append(f"akun lumbung_runner masih ada: {accounts!r}")
        # user backup baru bisa menjalankan backup, dan user lama tidak punya password yang diketahui
        sh(f"docker exec {NEW} bash -c 'mariadb-backup --backup --user=user_backup --password={env['BACKUP_PASS']} "
           "--stream=xbstream --target-dir=/tmp/bk >/dev/null'")
        # root hasil restore memakai password sumber ("pw"); user backup memang tidak boleh menulis
        prod("insert into appdb.t values (999999, 'tulis')", user="root", password="pw")
        if prod("select v from appdb.t where id=999999") != "tulis":
            problems.append("tidak bisa menulis di production")
        if "lumbung-standby" in sh("docker ps -a --format '{{.Names}}'"):
            problems.append("container database cadangan masih ada")
        if os.path.isdir(os.path.join(scratch, "standby", CONTAINER, "current")) or \
                os.path.exists(os.path.join(scratch, "standby", CONTAINER, "current.pw")):
            problems.append("folder current atau password runner tertinggal")
        info = sh(f"docker inspect {NEW} --format '{{{{.HostConfig.RestartPolicy.Name}}}} {{{{.Config.Hostname}}}}'").strip()
        if info != f"always {NEW}":
            problems.append(f"restart policy atau hostname salah: {info!r}")
        mounts = sh(f"docker inspect {NEW} --format '{{{{range .Mounts}}}}{{{{.Destination}}}} {{{{end}}}}'").split()
        if "/var/lib/mysql" not in mounts or "/backup" not in mounts:
            problems.append(f"mount tidak lengkap: {mounts}")
        agent_up = "lumbung/agent.py" in sh(f"docker exec {NEW} bash -c 'ps -eo args' ")
        print("agen berjalan:", agent_up)
        if not agent_up:
            problems.append("agen tidak berjalan di container production")
    # penaikan yang gagal mengembalikan database cadangan (port dipakai container lain)
    blocker = "standby-real-blocker"
    sh(f"docker rm -f {blocker} >/dev/null 2>&1 || true")
sh(f"docker rm -f -v {NEW} >/dev/null 2>&1 || true; docker volume rm -f {NEW}-backup >/dev/null 2>&1 || true")
r.purge_dir(os.path.join(promote_dir, NEW, "data"), config)
shutil.rmtree(promote_dir, ignore_errors=True)

# ---------- penaikan gagal: database cadangan dikembalikan utuh, termasuk akun runner ----------
status, extra = run_job("standby_full", [full] + [f for f in files if f["kind"] == "binlog"], {"port": PORT})
if status == "Lolos":
    sh(f"docker run -d --name {blocker} -p {NEW_PORT}:80 nginx:alpine >/dev/null")
    job = {"id": 78, "kind": "standby_promote", "container": CONTAINER, "files": [],
           "standby": {"applied_binlog_no": extra["standby"]["applied_binlog_no"], "port": PORT,
                       "promote": {"name": NEW, "port": NEW_PORT, "token": "t", "age_recipients": recipient}}}
    worker = r.StandbyRunner(config, FakeClient(), job, progress=lambda step: print("   ", step, flush=True))
    status, _ = worker.execute()
    print(f"penaikan gagal: {status} {worker.note or ''}")
    sh(f"docker rm -f {blocker} >/dev/null 2>&1 || true")
    if status == "Lolos":
        problems.append("penaikan seharusnya gagal karena port dipakai")
    else:
        alive = sh(f"docker inspect -f '{{{{.State.Running}}}}' {standby_name}").strip()
        got = read_standby("select count(*) from appdb.t")
        flags = read_standby("select @@global.read_only")
        print("setelah gagal: standby hidup", alive, "| baris", got, "| read_only", flags)
        if alive != "true" or got != expected_final.split()[0] or flags not in ("1", "ON"):
            problems.append("database cadangan tidak kembali seperti semula setelah penaikan gagal")
        if os.path.exists(os.path.join(promote_dir, NEW)):
            problems.append("folder container baru tertinggal setelah gagal")
        if sh(f"docker ps -aq --filter name=^{NEW}$").strip():
            problems.append("container baru tertinggal setelah gagal")
else:
    problems.append("full untuk uji penaikan gagal tidak lolos")
print("MASALAH:", problems or "tidak ada")
sh(f"docker rm -f -v {standby_name} {NEW} >/dev/null 2>&1 || true; docker volume rm -f {NEW}-backup >/dev/null 2>&1 || true")
r.purge_dir(folder, config)
shutil.rmtree(promote_dir, ignore_errors=True)
sys.exit(1 if problems else 0)
