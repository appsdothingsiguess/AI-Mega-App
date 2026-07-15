"""Async Ollama VRAM manager — serializes model swaps behind an asyncio lock."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("prompter.scheduler")

_scheduler: ModelScheduler | None = None


def _strip_ollama_prefix(model: str) -> str:
    if model.startswith("ollama/"):
        return model[len("ollama/") :]
    return model


class ModelScheduler:
    """Manages Ollama VRAM allocation. All local model calls go through this."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._url = settings.ollama.base_url.rstrip("/")
        self._lock = asyncio.Lock()
        self._loaded_main: str | None = None
        self._resident: set[str] = {
            _strip_ollama_prefix(settings.router.classifier),
            _strip_ollama_prefix(settings.embedding.model),
        }

    def _alias_to_ollama_name(self, alias: str) -> str:
        return self.settings.alias_to_ollama_name(alias)

    def _resolve_ollama_name(self, model: str) -> str:
        if model.startswith("local/"):
            return self._alias_to_ollama_name(model)
        return _strip_ollama_prefix(model)

    async def warmup_resident(self) -> None:
        """Force-load non-classifier resident models (embedding) at startup.

        The classifier is warmed separately via ``QwenClassifierAdapter.warmup()``
        with classifier-specific options. Generic ``_warmup`` must not touch it.
        """
        classifier_name = _strip_ollama_prefix(self.settings.router.classifier)
        async with self._lock:
            for ollama_name in self._resident:
                if not ollama_name or ollama_name == classifier_name:
                    continue
                await self._warmup(ollama_name)

    async def ensure_loaded(self, model: str) -> None:
        """Ensure model is loaded, evicting current main if needed."""
        ollama_name = self._resolve_ollama_name(model)
        if ollama_name in self._resident or ollama_name == self._loaded_main:
            return
        async with self._lock:
            if ollama_name == self._loaded_main:
                return
            if self._loaded_main and self._loaded_main not in self._resident:
                await self._unload(self._loaded_main)
            await self._warmup(ollama_name)
            self._loaded_main = ollama_name

    async def _unload(self, model: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self._url}/api/generate",
                json={"model": model, "keep_alive": 0},
            )

    def _warmup_request(self, model: str) -> tuple[str, dict[str, object]]:
        """Pick the correct Ollama endpoint for generate vs embed models."""
        keep_alive = self.settings.ollama.keep_alive
        embedding_name = _strip_ollama_prefix(self.settings.embedding.model)
        if model == embedding_name:
            return (
                f"{self._url}/api/embed",
                {"model": model, "input": "warmup", "keep_alive": keep_alive},
            )
        return (
            f"{self._url}/api/generate",
            {"model": model, "prompt": "", "keep_alive": keep_alive},
        )

    async def _warmup(
        self,
        model: str,
        retries: int = 3,
        backoff: float = 1.0,
    ) -> None:
        """Load model with retry + exponential backoff."""
        url, payload = self._warmup_request(model)
        delay = backoff
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    return
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                if attempt < retries - 1:
                    logger.warning(
                        "Ollama warmup attempt %s failed: %s. Retrying in %ss",
                        attempt + 1,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise


def get_model_scheduler(settings: Settings | None = None) -> ModelScheduler:
    """Return the process-wide ModelScheduler singleton."""
    global _scheduler
    if _scheduler is None:
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
        _scheduler = ModelScheduler(settings)
    return _scheduler


def reset_model_scheduler() -> None:
    """Clear the singleton (for tests)."""
    global _scheduler
    _scheduler = None
