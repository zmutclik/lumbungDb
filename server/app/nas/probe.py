"""Proses kecil pemeriksa NAS. Hanya proses ini yang menyentuh NAS untuk pemantauan, karena mount `hard` bisa membuat
pembacanya menunggu tanpa batas. Dijalankan terpisah dengan `python -I`; tanpa impor dari aplikasi."""
import json
import os
import sys


def main() -> None:
    data_dir, marker = sys.argv[1], sys.argv[2]
    result = {"mounted": True, "total": None, "free": None}
    if marker and not os.path.exists(os.path.join(data_dir, marker)):
        result["mounted"] = False
    else:
        stat = os.statvfs(data_dir)
        result["total"] = stat.f_blocks * stat.f_frsize
        result["free"] = stat.f_bavail * stat.f_frsize
    print(json.dumps(result))


if __name__ == "__main__":
    main()
