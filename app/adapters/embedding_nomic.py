"""Nomic embedding adapter via Ollama /api/embed."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import httpx

from app.protocols import EmbeddingService

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("prompter.embedding")

_HARD_LIMIT = 2048
_TOKEN_ESTIMATE_FACTOR = 1.3


def _strip_ollama_prefix(model: str) -> str:
    if model.startswith("ollama/"):
        return model[len("ollama/") :]
    return model


class NomicEmbeddingAdapter(EmbeddingService):
    """EmbeddingService implementation using Ollama nomic-embed-text."""

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._base_url = settings.ollama.base_url.rstrip("/")

    def max_tokens(self) -> int:
        return _HARD_LIMIT

    def _resolve_model(self) -> str:
        return _strip_ollama_prefix(self._settings.embedding.model)

    def _estimate_tokens(self, text: str) -> float:
        return len(text.split()) * _TOKEN_ESTIMATE_FACTOR

    def _validate_lengths(self, texts: list[str]) -> None:
        limit = self.max_tokens()
        for index, text in enumerate(texts):
            estimate = self._estimate_tokens(text)
            if estimate > limit:
                raise ValueError(
                    f"Text at index {index} exceeds embedding limit: "
                    f"estimated {estimate:.0f} tokens (max {limit})"
                )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        self._validate_lengths(texts)

        model = self._resolve_model()
        estimates = [self._estimate_tokens(text) for text in texts]
        logger.info(
            "Embedding %d text(s), token estimates: %s",
            len(texts),
            [f"{estimate:.0f}" for estimate in estimates],
        )

        start = time.perf_counter()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/api/embed",
                json={
                    "model": model,
                    "input": texts,
                    "keep_alive": self._settings.ollama.keep_alive,
                },
            )
            response.raise_for_status()
            data = response.json()

        latency_ms = (time.perf_counter() - start) * 1000
        logger.info("Embedding request completed in %.1fms", latency_ms)

        return data["embeddings"]
