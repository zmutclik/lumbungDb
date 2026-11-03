#!/usr/bin/env python3
"""Uji sungguhan runner terhadap Docker: membuat backup asli (full, dump, _users, binlog) dari container MariaDB,
mengenkripsinya dengan age, lalu menjalankan Restorer. Tanpa LumbungDB: unduhan diganti penyalinan file.
Pemakaian:  runner_real.py IMAGE [--corrupt]   (--corrupt memotong full, hasil yang diharapkan Gagal)"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runner"))
import runner as r  # noqa: E402

image = sys.argv[1]
corrupt = "--corrupt" in sys.argv
base = tempfile.mkdtemp(prefix="runner-real-")
os.chmod(base, 0o755)
nas, scratch = os.path.join(base, "nas"), os.path.join(base, "scratch")
os.makedirs(nas)
os.makedirs(scratch)


def sh(cmd, **kw):
    done = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)
    if done.returncode != 0:
        sys.exit(f"gagal: {cmd}\n{done.stdout}\n{done.stderr}")
    return done.stdout


keygen = sh(f"docker run --rm --entrypoint age-keygen {image}")
identity = os.path.join(base, "age.key")
with open(identity, "w") as handle:
    handle.write(keygen)
recipient = [line.split()[-1] for line in keygen.splitlines() if "public key" in line][0]

source = "runner-real-source"
sh(f"docker rm -f {source} >/dev/null 2>&1 || true")
sh(f"docker run -d --name {source} -e MARIADB_ROOT_PASSWORD=pw {image} --log-bin=mysql-bin")
try:
    for _ in range(120):
        if subprocess.run(f"docker exec {source} mariadb -uroot -ppw -h127.0.0.1 -e 'select 1'", shell=True,
                          capture_output=True).returncode == 0 and "init process done" in subprocess.run(
                f"docker logs {source}", shell=True, capture_output=True, text=True).stderr:
            break
        subprocess.run("sleep 1", shell=True)
    sql = ("create database appdb; create table appdb.t(id int primary key, v varchar(20)) engine=innodb; "
           "insert into appdb.t values (1,'a'),(2,'b'),(3,'c'); create database other; "
           "create table other.u(id int primary key) engine=innodb; insert into other.u values (1); "
           "create user 'tester'@'%' identified by 'x'; grant select on appdb.* to 'tester'@'%'; flush logs;")
    sh(f"docker exec {source} mariadb -uroot -ppw -h127.0.0.1 -e \"{sql}\"")

    def encrypt(cmd, name):
        sh(f"docker exec {source} bash -c \"{cmd} | gzip | age -r {recipient}\" > {nas}/{name}")
        return os.path.join(nas, name)

    files = []

    def add(kind, path, **extra):
        data = open(path, "rb").read()
        files.append({"id": len(files) + 1, "kind": kind, "relpath": os.path.basename(path), "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "database": None, "binlog_no": None,
                      "binlog_start": None, "chain_id": 1, **extra})

    full_path = encrypt("mariadb-backup --backup --user=root --password=pw --host=127.0.0.1 --stream=xbstream "
                        "--target-dir=/tmp/bk 2>/dev/null", "full.xbstream.gz.age")
    if corrupt:
        with open(full_path, "r+b") as handle:
            handle.truncate(os.path.getsize(full_path) // 2)
    add("fullbackup", full_path, binlog_start="mysql-bin.000001")
    add("databases", encrypt("mariadb-dump -uroot -ppw -h127.0.0.1 --databases appdb", "appdb.sql.gz.age"), database="appdb")
    add("databases", encrypt("mariadb-dump -uroot -ppw -h127.0.0.1 --databases other", "other.sql.gz.age"), database="other")
    users = ("mariadb -uroot -ppw -h127.0.0.1 -BN -e \\\"select concat('create user if not exists ', quote(user), '@', "
             "quote(host), ';') from mysql.user where user='tester'\\\"")
    add("databases", encrypt(users, "users.sql.gz.age"), database="_users")
    for number in (1, 2):
        add("binlog", encrypt("echo '-- binlog'", f"mysql-bin.00000{number}.gz.age"), binlog_no=number)
finally:
    sh(f"docker rm -f {source}")


class Local:
    def download(self, job_id, entry, dest):
        shutil.copy(os.path.join(nas, entry["relpath"]), dest)
        if hashlib.sha256(open(dest, "rb").read()).hexdigest() != entry["sha256"]:
            raise r.Failed(f"checksum {entry['relpath']}", "SHA256 berbeda")


config = {"scratch_dir": scratch, "age_identity": identity, "image": image, "ready_seconds": 180}
steps = []
status, results = r.Restorer(config, Local(), {"id": 1, "files": files}, progress=steps.append).execute()
print("status:", status)
for item in results:
    print(f"  {item['kind']:7} {item.get('database') or '':8} {item['status']:17} {item.get('failed_step') or ''} "
          f"{(item.get('log') or '')[:120]}")
leftover = sh("docker ps -aq --filter label=lumbung.runner=1").split()
print("container tersisa:", len(leftover), "| folder kerja tersisa:", os.listdir(scratch))
sys.exit(0 if (status == "Gagal") == corrupt and not leftover and not os.listdir(scratch) else 1)
