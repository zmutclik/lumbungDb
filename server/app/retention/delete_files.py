"""Proses kecil penghapus file di NAS, dijalankan dengan `python -I` seperti probe NAS. Hanya proses ini yang menyentuh
file untuk dihapus, karena mount `hard` bisa membuatnya menunggu tanpa batas. Tanpa impor dari aplikasi.
Masukan (stdin): {"root": "...", "paths": ["container/fullbackup/x.age", ...]}
Keluaran: {"deleted": [...], "missing": [...], "failed": {path: alasan}}"""
import json
import os
import sys


def main() -> None:
    request = json.load(sys.stdin)
    root = os.path.realpath(request["root"])
    result = {"deleted": [], "missing": [], "failed": {}}
    for relative in request["paths"]:
        # Hanya file biasa di bawah <root>/<container>/<jenis>/...; tanpa symlink dan tanpa keluar dari root.
        path = os.path.join(root, relative)
        parts = relative.split("/")
        if len(parts) < 3 or ".." in parts or relative.startswith("/") or os.path.basename(relative).startswith("."):
            result["failed"][relative] = "path tidak valid"
            continue
        if os.path.commonpath([root, os.path.realpath(os.path.dirname(path))]) != root:
            result["failed"][relative] = "keluar dari root"
            continue
        try:
            if os.path.islink(path) or (os.path.exists(path) and not os.path.isfile(path)):
                result["failed"][relative] = "bukan file biasa"
            elif not os.path.exists(path):
                result["missing"].append(relative)
            else:
                os.unlink(path)
                result["deleted"].append(relative)
        except OSError as exc:
            result["failed"][relative] = type(exc).__name__
    print(json.dumps(result))


if __name__ == "__main__":
    main()
