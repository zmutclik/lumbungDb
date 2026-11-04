"""Normalisasi query sebelum apa pun dikirim ke model: semua nilai literal diganti `?`.

Nilai di `WHERE` atau `VALUES` bisa berupa nomor rekam medis atau nama pasien, jadi pemindai ini berprinsip membuang,
bukan mengenali: string, angka, hex, dan komentar dihapus. Teks yang tidak bisa dibaca dengan utuh (mis. kutip yang tak
tertutup) dipotong di sana, dan sisanya dianggap literal. Pemindai karakter, bukan regex atas seluruh teks, supaya angka di
dalam pengenal seperti `t1` atau `kolom_2` tidak ikut diganti dan kutip di dalam string tidak membingungkan."""
import re

MAX_LENGTH = 300
_IDENT_START = re.compile(r"[A-Za-z_$\u0080-￿]")
_IDENT_PART = re.compile(r"[A-Za-z0-9_$\u0080-￿]")
_NUMBER = re.compile(r"(?:0[xX][0-9A-Fa-f]+|0[bB][01]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_IN_LIST = re.compile(r"\(\s*\?(?:\s*,\s*\?)+\s*\)")
_ROWS = re.compile(r"\(\?\)(?:\s*,\s*\(\?\))+")


def _skip_string(text: str, i: int) -> int:
    """Indeks sesudah kutip penutup; panjang teks bila tidak tertutup."""
    quote = text[i]
    i += 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == quote:
            if text[i + 1:i + 2] == quote:       # kutip ganda di dalam string
                i += 2
                continue
            return i + 1
        i += 1
    return len(text)


def normalize_query(sql: str) -> str:
    text = sql or ""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        two = text[i:i + 2]
        if two == "/*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        elif two == "--" and (i + 2 >= n or text[i + 2] in " \t\r\n") or char == "#":
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
            out.append(" ")
        elif char in "'\"":
            i = _skip_string(text, i)
            out.append("?")
        elif char == "`":
            end = text.find("`", i + 1)
            end = n - 1 if end == -1 else end
            out.append(text[i:end + 1])
            i = end + 1
        elif _IDENT_START.match(char):
            j = i + 1
            while j < n and _IDENT_PART.match(text[j]):
                j += 1
            word = text[i:j]
            # x'ab' dan b'01' adalah literal; literal charset (_utf8'...') dibuang kutipnya di cabang string
            if word.lower() in ("x", "b", "n") and text[j:j + 1] == "'":
                i = _skip_string(text, j)
                out.append("?")
            elif word.startswith("_") and text[j:j + 1] == "'":
                i = _skip_string(text, j)
                out.append("?")
            else:
                out.append(word)
                i = j
        elif char.isdigit() or (char == "." and text[i + 1:i + 2].isdigit()):
            match = _NUMBER.match(text, i)
            i = match.end()
            out.append("?")
        else:
            out.append(char)
            i += 1
    result = re.sub(r"\s+", " ", "".join(out)).strip()
    result = re.sub(r"(?<=[=<>(,\s])[-+]\s*\?", "?", result)       # tanda negatif ikut literalnya
    result = _IN_LIST.sub("(?)", result)
    result = _ROWS.sub("(?)", result)
    return result[:MAX_LENGTH]
