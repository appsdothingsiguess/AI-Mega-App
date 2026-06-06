"""Qdrant vector store adapter."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.types import SearchResult

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("prompter.rag")

DEFAULT_COLLECTION = "prompter_docs"
VECTOR_SIZE = 768


def _point_id(id_str: str) -> str:
    """Map caller IDs to stable UUID strings for Qdrant."""
    try:
        uuid.UUID(id_str)
        return id_str
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, id_str))


def _build_filter(filter_dict: dict | None) -> Filter | None:
    if not filter_dict:
        return None

    conditions: list[FieldCondition] = []
    for key, value in filter_dict.items():
        if isinstance(value, list):
            conditions.append(
                FieldCondition(key=key, match=MatchAny(any=value))
            )
        else:
            conditions.append(
                FieldCondition(key=key, match=MatchValue(value=value))
            )

    return Filter(must=conditions) if conditions else None


def _normalize_score(raw_score: float) -> float:
    """Clamp cosine similarity to [0, 1]."""
    return max(0.0, min(1.0, raw_score))


def _payload_from_metadata(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    source = str(metadata.get("source", ""))
    title = str(metadata.get("title", ""))
    extra = {k: v for k, v in metadata.items() if k not in ("source", "title")}
    return {
        "text": text,
        "source": source,
        "title": title,
        "metadata": extra,
    }


def _search_result_from_payload(payload: dict[str, Any], score: float) -> SearchResult:
    nested = payload.get("metadata")
    if isinstance(nested, dict):
        metadata = dict(nested)
    else:
        metadata = {}

    return SearchResult(
        text=str(payload.get("text", "")),
        source=str(payload.get("source", "")),
        title=str(payload.get("title", "")),
        score=_normalize_score(score),
        metadata=metadata,
    )


class QdrantAdapter:
    """VectorStore implementation backed by Qdrant."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._collection = collection_name
        self._client = AsyncQdrantClient(
            url=settings.qdrant.url.rstrip("/"),
            check_compatibility=False,
        )
        self._collection_ready = False

    async def _ensure_collection(self, *, create: bool = False) -> bool:
        """Return True when the collection exists (optionally creating it)."""
        if self._collection_ready:
            return True

        exists = await self._client.collection_exists(self._collection)
        if not exists:
            if not create:
                return False
            logger.info(
                "Creating Qdrant collection %r (%d dims, cosine)",
                self._collection,
                VECTOR_SIZE,
            )
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name="source",
                field_schema=PayloadSchemaType.KEYWORD,
            )

        self._collection_ready = True
        return True

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return

        if not (
            len(ids) == len(embeddings) == len(texts) == len(metadatas)
        ):
            raise ValueError(
                "ids, embeddings, texts, and metadatas must have the same length"
            )

        await self._ensure_collection(create=True)

        points = [
            PointStruct(
                id=_point_id(point_id),
                vector=embedding,
                payload=_payload_from_metadata(text, metadata),
            )
            for point_id, embedding, text, metadata in zip(
                ids, embeddings, texts, metadatas, strict=True
            )
        ]

        await self._client.upsert(collection_name=self._collection, points=points)
        logger.info("Upserted %d point(s) into %r", len(points), self._collection)

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        filter: dict | None = None,
    ) -> list[SearchResult]:
        if not await self._ensure_collection():
            return []

        response = await self._client.query_points(
            collection_name=self._collection,
            query=query_embedding,
            limit=top_k,
            query_filter=_build_filter(filter),
        )

        return [
            _search_result_from_payload(hit.payload or {}, hit.score or 0.0)
            for hit in response.points
        ]

    async def delete(self, ids: list[str]) -> None:
        if not ids:
            return

        if not await self._ensure_collection():
            return

        point_ids = [_point_id(point_id) for point_id in ids]
        await self._client.delete(
            collection_name=self._collection,
            points_selector=point_ids,
        )
        logger.info("Deleted %d point(s) from %r", len(point_ids), self._collection)

    async def count(self, filter: dict | None = None) -> int:
        if not await self._ensure_collection():
            return 0

        result = await self._client.count(
            collection_name=self._collection,
            count_filter=_build_filter(filter),
            exact=True,
        )
        return int(result.count)

    async def close(self) -> None:
        await self._client.close()

    async def drop_collection(self) -> None:
        """Remove the collection (test helper)."""
        if await self._client.collection_exists(self._collection):
            await self._client.delete_collection(self._collection)
        self._collection_ready = False
