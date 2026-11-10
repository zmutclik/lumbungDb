"""Penyaring teks log sebelum dikirim ke model. Log mentah bisa memuat nilai data: pesan kesalahan SQL seperti
`Duplicate entry 'Budi' for key`, nama file dengan nomor RM, atau alamat surel. Prinsipnya sama dengan normalisasi query:
membuang, bukan mengenali. Isi di dalam kutip, deretan angka panjang, alamat surel, alamat IP, dan deretan heksadesimal
panjang diganti penanda. Kata dan angka pendek (kode kesalahan, nomor baris, jumlah) tetap supaya log masih bisa dibaca."""
import re

from app.ai.normalize import _skip_string

MAX_LINE = 300
MAX_LINES = 20
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_LONG_DIGITS = re.compile(r"\d{6,}")
_PATH_DIGITS = re.compile(r"(?<=[/_-])\d{4,}(?=[/_.-]|$)")


def _strip_quotes(line: str) -> str:
    out, i = [], 0
    while i < len(line):
        if line[i] in "'\"`":
            quote = line[i]
            end = _skip_string(line, i)
            unterminated = end >= len(line) and (end - 1 < i + 1 or line[end - 1] != quote)
            out.append(f"{quote}?{quote}" if not unterminated else f"{quote}?")
            i = end
        else:
            out.append(line[i])
            i += 1
    return "".join(out)


def scrub_line(line: str) -> str:
    text = _strip_quotes(line or "")
    text = _EMAIL.sub("<surel>", text)
    text = _IP.sub("<ip>", text)
    text = _LONG_HEX.sub("<hex>", text)
    text = _PATH_DIGITS.sub("?", text)
    text = _LONG_DIGITS.sub("?", text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_LINE]


def scrub_log(text: str | list | None, max_lines: int = MAX_LINES) -> list[str]:
    """Baris-baris terakhir yang sudah disaring; baris kosong dibuang."""
    if text is None:
        return []
    lines = text if isinstance(text, list) else str(text).splitlines()
    cleaned = [scrub_line(str(line)) for line in lines[-max_lines * 3:]]
    return [line for line in cleaned if line][-max_lines:]
