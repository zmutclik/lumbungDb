"""Penyedia model sebagai modul yang bisa diganti lewat konfigurasi (`AI_PROVIDER`). Semua penyedia hanya menerima
teks yang sudah disaring oleh app.ai.service, dan mengembalikan teks. Menambah penyedia baru = menambah kelas dan satu
baris di PROVIDERS."""
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)
ANTHROPIC_VERSION = "2023-06-01"


class ProviderError(Exception):
    """Kegagalan penyedia; pesannya aman disimpan dan ditampilkan (tanpa kunci API)."""


@dataclass
class Provider:
    name = "dasar"
    model: str = ""

    async def complete(self, system: str, prompt: str, client: httpx.AsyncClient) -> str:
        raise NotImplementedError


class ClaudeProvider(Provider):
    name = "claude"

    async def complete(self, system: str, prompt: str, client: httpx.AsyncClient) -> str:
        if not settings.AI_API_KEY:
            raise ProviderError("AI_API_KEY belum diisi")
        try:
            response = await client.post(
                f"{settings.AI_BASE_URL.rstrip('/')}/v1/messages", timeout=settings.AI_TIMEOUT_SECONDS,
                headers={"x-api-key": settings.AI_API_KEY, "anthropic-version": ANTHROPIC_VERSION},
                json={"model": self.model, "max_tokens": settings.AI_MAX_TOKENS, "system": system,
                      "messages": [{"role": "user", "content": prompt}]})
        except httpx.HTTPError as err:
            raise ProviderError(f"tidak terhubung ke penyedia: {type(err).__name__}") from err
        if response.status_code != 200:
            raise ProviderError(f"penyedia menjawab HTTP {response.status_code}")
        try:
            blocks = response.json()["content"]
            return "".join(b["text"] for b in blocks if b.get("type") == "text").strip()
        except (ValueError, KeyError, TypeError) as err:
            raise ProviderError("jawaban penyedia tidak dikenali") from err


class OllamaProvider(Provider):
    name = "ollama"

    async def complete(self, system: str, prompt: str, client: httpx.AsyncClient) -> str:
        try:
            response = await client.post(
                f"{settings.AI_BASE_URL.rstrip('/')}/api/chat", timeout=settings.AI_TIMEOUT_SECONDS,
                json={"model": self.model, "stream": False,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        except httpx.HTTPError as err:
            raise ProviderError(f"tidak terhubung ke penyedia: {type(err).__name__}") from err
        if response.status_code != 200:
            raise ProviderError(f"penyedia menjawab HTTP {response.status_code}")
        try:
            return str(response.json()["message"]["content"]).strip()
        except (ValueError, KeyError, TypeError) as err:
            raise ProviderError("jawaban penyedia tidak dikenali") from err


class OpenAIProvider(Provider):
    """API `/chat/completions` yang dipakai banyak gateway dan server lokal (OpenAI, vLLM, LiteLLM, Ollama `/v1`).
    AI_BASE_URL sudah memuat `/v1`, mis. https://gateway.contoh/v1. Kunci dikirim sebagai Bearer bila diisi."""

    name = "openai"

    async def complete(self, system: str, prompt: str, client: httpx.AsyncClient) -> str:
        headers = {"Authorization": f"Bearer {settings.AI_API_KEY}"} if settings.AI_API_KEY else {}
        try:
            response = await client.post(
                f"{settings.AI_BASE_URL.rstrip('/')}/chat/completions", timeout=settings.AI_TIMEOUT_SECONDS,
                headers=headers,
                json={"model": self.model, "max_tokens": settings.AI_MAX_TOKENS, "stream": False,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        except httpx.HTTPError as err:
            raise ProviderError(f"tidak terhubung ke penyedia: {type(err).__name__}") from err
        if response.status_code != 200:
            raise ProviderError(f"penyedia menjawab HTTP {response.status_code}")
        try:
            return str(response.json()["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError) as err:
            raise ProviderError("jawaban penyedia tidak dikenali") from err


class FakeProvider(Provider):
    """Untuk pengembangan dan uji: tanpa jaringan, jawabannya menyebut jumlah container yang diterima."""

    name = "fake"

    async def complete(self, system: str, prompt: str, client: httpx.AsyncClient) -> str:
        if "GAGALKAN" in prompt:
            raise ProviderError("penyedia uji dipaksa gagal")
        return f"Ringkasan uji ({len(prompt)} karakter masukan)."


PROVIDERS: dict[str, type[Provider]] = {"claude": ClaudeProvider, "openai": OpenAIProvider, "ollama": OllamaProvider,
                                         "fake": FakeProvider}


def get_provider() -> Provider | None:
    """None bila AI mati (bawaan). Nama yang tidak dikenal dianggap mati dan dicatat, supaya salah ketik tidak
    menghentikan laporan."""
    name = settings.AI_PROVIDER.strip().lower()
    if name in ("", "none"):
        return None
    cls = PROVIDERS.get(name)
    if cls is None:
        logger.error("ai_provider_unknown", extra={"provider": name})
        return None
    return cls(model=settings.AI_MODEL)
