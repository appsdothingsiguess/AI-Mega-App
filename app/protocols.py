"""Runtime-checkable service Protocol interfaces."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.types import ClassifierOutput, SearchResult


@runtime_checkable
class EmbeddingService(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts. Raises ValueError if any text exceeds max_tokens().

        Callers MUST pre-check length. Adapter MUST validate and reject,
        never silently truncate.
        """
        ...

    def max_tokens(self) -> int:
        """Return the adapter hard token limit (sync constant)."""
        ...


@runtime_checkable
class SearchService(Protocol):
    async def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        ...


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        ...

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        filter: dict | None = None,
    ) -> list[SearchResult]:
        ...

    async def delete(self, ids: list[str]) -> None:
        ...

    async def count(self, filter: dict | None = None) -> int:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class VisionService(Protocol):
    async def analyze(self, image_bytes: bytes, prompt: str) -> str:
        ...

    async def analyze_multi(self, images: list[bytes], prompt: str) -> str:
        ...


@runtime_checkable
class Classifier(Protocol):
    async def classify(self, message: str) -> ClassifierOutput:
        ...


@runtime_checkable
class PDFGenerator(Protocol):
    async def generate(self, content: str, format: str) -> bytes:
        ...
