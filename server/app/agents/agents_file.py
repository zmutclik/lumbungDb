"""config/agents.conf: satu-satunya tempat token agen dan sumber status aktif.

Dibaca ulang saat file berubah, supaya semua proses web melihat isi yang sama (bukan state di memori satu proses).
Penulisan atomik lewat rename di folder yang sama, dengan kunci antarproses. Karena itu compose memasang folder
config/, bukan satu file: file bind-mount tunggal tidak bisa diganti dengan rename.
"""
import fcntl
import hmac
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TOKEN_MIN_LENGTH = 16
RESERVED_NAMES = {"new", "tambah"}
_LINE = re.compile(r"^(?P<name>[^\s=#]+)\s*=\s*(?P<token>\S+)$")
_COMMENTED = re.compile(r"^#\s*(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})\s*=\s*(?P<token>\S{16,})$")


class AgentsFileError(Exception):
    """Isi file tidak valid. LumbungDB gagal start dengan pesan yang menyebut nomor baris."""


class AgentsFileWriteError(Exception):
    """File tidak bisa ditulis; daftar agen tidak berubah."""


class NameRejected(ValueError):
    pass


def project_name(name: str) -> str:
    """Nama project compose. Dua container dengan project sama akan berbagi volume bila ada di satu server."""
    return re.sub(r"[^a-z0-9_-]", "-", name.lower())


@dataclass(frozen=True)
class AgentEntries:
    active: dict[str, str]      # nama → token
    disabled: frozenset[str]    # baris yang dikomentari

    @property
    def all_names(self) -> set[str]:
        return set(self.active) | set(self.disabled)


def parse(text: str) -> AgentEntries:
    active: dict[str, str] = {}
    disabled: set[str] = set()
    projects: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            commented = _COMMENTED.match(line)
            if commented:
                disabled.add(commented.group("name"))
            continue
        match = _LINE.match(line)
        if not match or not NAME.fullmatch(match.group("name")):
            raise AgentsFileError(f"agents.conf baris {number}: format harus 'nama = token' dengan nama yang valid")
        name, token = match.group("name"), match.group("token")
        if len(token) < TOKEN_MIN_LENGTH:
            raise AgentsFileError(f"agents.conf baris {number}: token kurang dari {TOKEN_MIN_LENGTH} karakter")
        if name in active:
            raise AgentsFileError(f"agents.conf baris {number}: nama {name} ganda")
        project = project_name(name)
        if project in projects:
            raise AgentsFileError(
                f"agents.conf baris {number}: {name} dan {projects[project]} menghasilkan project compose {project}"
            )
        projects[project] = name
        active[name] = token
    return AgentEntries(active, frozenset(disabled - set(active)))


class AgentRegistry:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._signature: tuple[int, int] | None = None
        self._entries = AgentEntries({}, frozenset())

    def load(self) -> AgentEntries:
        """Dipakai saat start: isi tidak valid = gagal start. File belum ada = daftar kosong."""
        self._read(strict=True)
        return self._entries

    def entries(self) -> AgentEntries:
        self._read(strict=False)
        return self._entries

    def _read(self, *, strict: bool) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._signature, self._entries = None, AgentEntries({}, frozenset())
            return
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature and not strict:
            return
        try:
            self._entries = parse(self.path.read_text())
            self._signature = signature
        except AgentsFileError:
            if strict:
                raise
            # Diedit salah saat berjalan: agen lama tetap dilayani, kesalahannya muncul saat restart.
            logger.exception("agents_file_invalid")
            self._signature = signature

    def authenticate(self, token: str | None) -> str | None:
        if not token:
            return None
        found = None
        for name, expected in self.entries().active.items():   # tanpa break: waktu tidak bergantung pada posisi
            if hmac.compare_digest(expected.encode(), token.encode()):
                found = name
        return found

    def _rewrite(self, change) -> None:
        directory = self.path.parent
        try:
            with open(directory / ".agents.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    current = self.path.read_text()
                    mode = self.path.stat().st_mode & 0o777
                except FileNotFoundError:
                    current, mode = "", 0o600
                new_text = change(current)
                fd, tmp = tempfile.mkstemp(dir=directory, prefix=".agents-")
                try:
                    with os.fdopen(fd, "w") as handle:
                        handle.write(new_text)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(tmp, mode)
                    os.replace(tmp, self.path)
                except BaseException:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
        except OSError as err:
            raise AgentsFileWriteError(str(err)) from err
        self._read(strict=False)

    def name_error(self, name: str, entries: AgentEntries | None = None) -> str | None:
        entries = entries or self.entries()
        if not name:
            return "Isi nama container."
        if not NAME.fullmatch(name):
            return "Hanya huruf, angka, _ . - dan harus diawali huruf atau angka, maksimal 64 karakter."
        if name.lower() in RESERVED_NAMES:
            return f"Nama {name} dipakai oleh aplikasi."
        for other in entries.all_names:
            if other.lower() == name.lower():
                return f"Nama {name} sudah terdaftar."
            if project_name(other) == project_name(name):
                return f"Bentrok dengan {other}: keduanya menjadi project compose {project_name(name)}."
        return None

    def add(self, name: str, *, extra_names: set[str] | frozenset[str] = frozenset()) -> str:
        """Menulis baris baru dan mengembalikan token. Satu-satunya saat token keluar dari file."""
        token = secrets.token_urlsafe(24)

        def change(current: str) -> str:
            entries = parse(current)
            error = self.name_error(name, entries)
            if error is None:
                for other in extra_names:
                    if project_name(other) == project_name(name):
                        error = f"Nama {name} sudah terdaftar."
            if error:
                raise NameRejected(error)
            prefix = current if current.endswith("\n") or not current else current + "\n"
            return f"{prefix}{name} = {token}\n"

        self._rewrite(change)
        return token

    def disable(self, name: str) -> bool:
        """Memberi # di baris agen. Token tidak diubah dan baris tidak dihapus."""
        changed = False

        def change(current: str) -> str:
            nonlocal changed
            lines = current.splitlines(keepends=True)
            for index, raw in enumerate(lines):
                match = _LINE.match(raw.strip())
                if match and match.group("name") == name:
                    lines[index] = "# " + raw
                    changed = True
            return "".join(lines)

        self._rewrite(change)
        return changed

    def remove_pending(self, name: str) -> bool:
        """Hanya untuk Batalkan pendaftaran, sebelum agen pernah mengirim heartbeat."""
        changed = False

        def change(current: str) -> str:
            nonlocal changed
            kept = []
            for raw in current.splitlines(keepends=True):
                match = _LINE.match(raw.strip())
                if match and match.group("name") == name:
                    changed = True
                    continue
                kept.append(raw)
            return "".join(kept)

        self._rewrite(change)
        return changed
