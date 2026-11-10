"""Proses kecil pemindai NAS, dijalankan dengan `python -I`: hanya proses ini yang membaca isi file di NAS untuk
pindai ulang, karena mount `hard` bisa membuatnya menunggu tanpa batas. Tanpa impor dari aplikasi.
Keluaran: satu baris JSON per file, jadi induknya bisa mendeteksi proses yang macet dari jeda antar baris."""
import hashlib
import json
import os
import re
import sys

KINDS = ("fullbackup", "databases", "binlog")
NAME = re.compile(r"\.gz\.age(\.v\d+-[0-9a-f]{8})?$")
CHUNK = 1024 * 1024


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def emit(container: str, kind: str, database: str | None, directory: str, name: str, root: str) -> None:
    path = os.path.join(directory, name)
    if name.startswith(".") or os.path.islink(path) or not os.path.isfile(path) or not NAME.search(name):
        return
    stat = os.stat(path)
    print(json.dumps({"container": container, "kind": kind, "database": database,
                      "stored_path": os.path.relpath(path, os.path.join(root, container)), "size": stat.st_size,
                      "mtime": int(stat.st_mtime), "sha256": sha256_of(path)}), flush=True)


def main() -> None:
    root = sys.argv[1]
    for container in sorted(os.listdir(root)):
        base = os.path.join(root, container)
        if container.startswith(".") or not os.path.isdir(base) or os.path.islink(base):
            continue
        for kind in KINDS:
            folder = os.path.join(base, kind)
            if not os.path.isdir(folder):
                continue
            if kind == "databases":
                for database in sorted(os.listdir(folder)):
                    sub = os.path.join(folder, database)
                    if os.path.isdir(sub) and not os.path.islink(sub):
                        for name in sorted(os.listdir(sub)):
                            emit(container, kind, database, sub, name, root)
            else:
                for name in sorted(os.listdir(folder)):
                    emit(container, kind, None, folder, name, root)
    print(json.dumps({"done": True}), flush=True)


if __name__ == "__main__":
    main()
