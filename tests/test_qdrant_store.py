"""Integration tests for app/adapters/qdrant_store.py."""

from __future__ import annotations

import math
import uuid
from typing import AsyncIterator

import httpx
import pytest

from app.adapters.qdrant_store import DEFAULT_COLLECTION, QdrantAdapter, VECTOR_SIZE
from app.config import Settings
from app.protocols import VectorStore


def _settings(**overrides: object) -> Settings:
    return Settings(projects_dir="./projects", data_dir="./data", **overrides)


def _unit_vector(index: int, dim: int = VECTOR_SIZE) -> list[float]:
    vector = [0.0] * dim
    vector[index % dim] = 1.0
    return vector


def _similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _qdrant_available(url: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{url.rstrip('/')}/collections", timeout=2.0)
            return response.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


@pytest.fixture
async def qdrant_adapter() -> AsyncIterator[QdrantAdapter]:
    settings = _settings()
    collection = f"test_prompter_{uuid.uuid4().hex}"
    adapter = QdrantAdapter(settings, collection_name=collection)

    if not await _qdrant_available(settings.qdrant.url):
        pytest.skip(f"Qdrant unavailable at {settings.qdrant.url}")

    try:
        yield adapter
    finally:
        await adapter.drop_collection()
        await adapter.close()


@pytest.mark.asyncio
async def test_satisfies_vector_store_protocol(qdrant_adapter: QdrantAdapter) -> None:
    assert isinstance(qdrant_adapter, VectorStore)


@pytest.mark.asyncio
async def test_upsert_search_round_trip(qdrant_adapter: QdrantAdapter) -> None:
    vec_a = _unit_vector(0)
    vec_b = _unit_vector(1)
    await qdrant_adapter.upsert(
        ids=["doc-a", "doc-b"],
        embeddings=[vec_a, vec_b],
        texts=["Paris is the capital of France.", "Berlin is the capital of Germany."],
        metadatas=[
            {"source": "france.txt", "title": "France"},
            {"source": "germany.txt", "title": "Germany"},
        ],
    )

    results = await qdrant_adapter.search(query_embedding=vec_a, top_k=2)
    assert len(results) == 2
    assert results[0].text == "Paris is the capital of France."
    assert results[0].source == "france.txt"
    assert results[0].title == "France"
    assert results[0].score >= results[1].score
    assert 0.0 <= results[0].score <= 1.0


@pytest.mark.asyncio
async def test_search_filter_by_source(qdrant_adapter: QdrantAdapter) -> None:
    vec_a = _unit_vector(10)
    vec_b = _unit_vector(11)
    await qdrant_adapter.upsert(
        ids=["filter-a", "filter-b"],
        embeddings=[vec_a, vec_b],
        texts=["Alpha document.", "Beta document."],
        metadatas=[
            {"source": "alpha.txt"},
            {"source": "beta.txt"},
        ],
    )

    filtered = await qdrant_adapter.search(
        query_embedding=vec_b,
        top_k=5,
        filter={"source": "beta.txt"},
    )
    assert len(filtered) == 1
    assert filtered[0].source == "beta.txt"
    assert filtered[0].text == "Beta document."


@pytest.mark.asyncio
async def test_delete_removes_points(qdrant_adapter: QdrantAdapter) -> None:
    vec = _unit_vector(20)
    await qdrant_adapter.upsert(
        ids=["delete-me"],
        embeddings=[vec],
        texts=["Temporary chunk."],
        metadatas=[{"source": "temp.txt"}],
    )
    assert await qdrant_adapter.count() == 1

    await qdrant_adapter.delete(["delete-me"])
    assert await qdrant_adapter.count() == 0

    results = await qdrant_adapter.search(query_embedding=vec, top_k=1)
    assert results == []


@pytest.mark.asyncio
async def test_count_with_filter(qdrant_adapter: QdrantAdapter) -> None:
    await qdrant_adapter.upsert(
        ids=["count-1", "count-2", "count-3"],
        embeddings=[_unit_vector(30), _unit_vector(31), _unit_vector(32)],
        texts=["One", "Two", "Three"],
        metadatas=[
            {"source": "group-a.txt"},
            {"source": "group-a.txt"},
            {"source": "group-b.txt"},
        ],
    )

    assert await qdrant_adapter.count() == 3
    assert await qdrant_adapter.count(filter={"source": "group-a.txt"}) == 2
    assert await qdrant_adapter.count(filter={"source": "group-b.txt"}) == 1


@pytest.mark.asyncio
async def test_collection_created_with_source_payload_index(
    qdrant_adapter: QdrantAdapter,
) -> None:
    await qdrant_adapter.upsert(
        ids=["index-check"],
        embeddings=[_unit_vector(40)],
        texts=["Index probe."],
        metadatas=[{"source": "probe.txt"}],
    )

    info = await qdrant_adapter._client.get_collection(qdrant_adapter._collection)
    payload_schema = info.payload_schema or {}
    assert "source" in payload_schema


def test_default_collection_name_constant() -> None:
    assert DEFAULT_COLLECTION == "prompter_docs"


@pytest.mark.asyncio
async def test_upsert_length_mismatch_raises() -> None:
    adapter = QdrantAdapter(_settings(), collection_name="offline-test")
    with pytest.raises(ValueError, match="same length"):
        await adapter.upsert(
            ids=["only-one"],
            embeddings=[_unit_vector(50), _unit_vector(51)],
            texts=["Mismatch."],
            metadatas=[{"source": "x.txt"}],
        )
    await adapter.close()


@pytest.mark.asyncio
async def test_empty_upsert_and_delete_are_no_ops() -> None:
    adapter = QdrantAdapter(_settings(), collection_name="offline-test")
    await adapter.upsert([], [], [], [])
    await adapter.delete([])
    await adapter.close()


@pytest.mark.asyncio
async def test_search_result_metadata_round_trip(qdrant_adapter: QdrantAdapter) -> None:
    vec = _unit_vector(60)
    await qdrant_adapter.upsert(
        ids=["meta-id"],
        embeddings=[vec],
        texts=["Metadata test."],
        metadatas=[
            {
                "source": "meta.txt",
                "title": "Meta Title",
                "page": 3,
                "tags": ["a", "b"],
            }
        ],
    )

    results = await qdrant_adapter.search(query_embedding=vec, top_k=1)
    assert results[0].metadata == {"page": 3, "tags": ["a", "b"]}
    assert results[0].title == "Meta Title"


@pytest.mark.asyncio
async def test_score_reflects_vector_similarity(qdrant_adapter: QdrantAdapter) -> None:
    query = _unit_vector(70)
    near = _similarity(query, _unit_vector(70))
    far = _similarity(query, _unit_vector(71))

    await qdrant_adapter.upsert(
        ids=["near-id", "far-id"],
        embeddings=[_unit_vector(70), _unit_vector(71)],
        texts=["Near match.", "Far match."],
        metadatas=[{"source": "s1.txt"}, {"source": "s2.txt"}],
    )

    results = await qdrant_adapter.search(query_embedding=query, top_k=2)
    assert results[0].source == "s1.txt"
    assert results[0].score >= results[1].score
    assert abs(results[0].score - near) < 0.01
    assert abs(results[1].score - far) < 0.01
