"""Migrasi otomatis saat deploy: `python -m app.core.migrate`.

Dijalankan oleh service `migrate` di docker-compose, sekali, sebelum `web` dan `worker` start.
Satu proses saja yang menjalankan DDL: uvicorn multi-worker yang migrasi bersamaan bisa bentrok,
dan DDL MariaDB tidak transaksional.

- Database kosong → semua tabel dibuat.
- Versi skema lebih lama dari kode → di-upgrade ke head.
- Versi skema tidak dikenal kode (biasanya image lebih lama dari database) → berhenti dengan pesan jelas.
  Tidak ada downgrade otomatis: downgrade bisa menghapus kolom/tabel beserta datanya.
"""
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def main() -> int:
    config = Config(str(ALEMBIC_INI))
    try:
        command.upgrade(config, "head")
    except CommandError as exc:
        if "Can't locate revision" in str(exc):
            print(
                f"Migrasi dihentikan: {exc}\n"
                "Skema database lebih baru dari kode di image ini. Deploy image versi yang sama atau lebih baru; "
                "downgrade skema tidak dilakukan otomatis karena bisa menghapus data.",
                file=sys.stderr,
            )
            return 1
        raise
    print("Skema database sudah di versi terbaru.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
