"""Operasi berkas di NAS. Semua fungsi blocking dan bisa tersangkut bila NFS macet, jadi selalu dipanggil dari thread
dengan limiter upload (router), tidak pernah dari event loop."""
import hashlib
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

CHUNK = 1024 * 1024


@dataclass
class PartFile:
    path: Path
    final_base: Path
    handle: object
    hasher: "hashlib._Hash" = field(default_factory=hashlib.sha256)
    size: int = 0


def container_root(container: str) -> Path:
    root = Path(settings.DATA_DIR)
    target = (root / container).resolve()
    if target.parent != root.resolve():      # pengaman kedua sesudah validasi nama
        raise ValueError("nama container tidak valid")
    return target


def open_part(container: str, kind: str, relpath: str) -> PartFile:
    final_base = container_root(container) / kind / relpath
    if not final_base.resolve().is_relative_to(container_root(container)):
        raise ValueError("path keluar dari folder container")
    final_base.parent.mkdir(parents=True, exist_ok=True)
    # Tidak pernah ada file setengah jadi dengan nama final. Nama acak: upload ulang bersamaan tidak saling menimpa.
    path = final_base.with_name(f".{final_base.name}.{secrets.token_hex(6)}.part")
    return PartFile(path=path, final_base=final_base, handle=open(path, "xb"))


def write_chunk(part: PartFile, chunk: bytes) -> None:
    part.handle.write(chunk)
    part.hasher.update(chunk)
    part.size += len(chunk)


def finish_part(part: PartFile) -> str:
    part.handle.flush()
    os.fsync(part.handle.fileno())
    part.handle.close()
    return part.hasher.hexdigest()


def discard_part(part: PartFile) -> None:
    try:
        part.handle.close()
    finally:
        part.path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def place_part(part: PartFile, sha256: str, first_version: int, mtime: float) -> tuple[Path, int]:
    """Memberi nama akhir tanpa pernah menimpa: hard link gagal bila nama sudah ada. Mengembalikan (lokasi, versi).
    File yang sudah ada dengan isi sama dipakai ulang, misalnya setelah metadata hilang atau upload terputus."""
    os.utime(part.path, (mtime, mtime))      # pemindaian ulang bisa menghitung rantai kembali dari waktu ubah
    version = first_version
    while True:
        name = part.final_base.name if version == 1 else f"{part.final_base.name}.v{version}-{sha256[:8]}"
        target = part.final_base.with_name(name)
        try:
            os.link(part.path, target)
        except FileExistsError:
            if _sha256_file(target) == sha256:
                part.path.unlink(missing_ok=True)
                return target, version
            version += 1
            continue
        part.path.unlink(missing_ok=True)
        _fsync_dir(target.parent)
        return target, version
