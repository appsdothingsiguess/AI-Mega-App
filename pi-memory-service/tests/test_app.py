# ruff: noqa: E501
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app import Settings, create_app  # noqa: E402

TOKEN = "test-token"
PROJECT = r"C:\\work\\demo"


def client_for(database: Path) -> TestClient:
    app = create_app(Settings(database, TOKEN, frozenset({"testclient"})))
    return TestClient(app)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def claim(content: str, claim_type: str = "fact", tags: list[str] | None = None) -> dict[str, object]:
    return {"content": content, "type": claim_type, "tags": tags or []}


def create(client: TestClient, claims: list[dict[str, object]], session="s1", source="h1"):
    return client.post("/v1/memories", headers=auth(), json={"project_key": PROJECT, "session_id": session, "source_hash": source, "claims": claims})


def test_create_preserves_provenance_and_reports_created_id(tmp_path: Path):
    client = client_for(tmp_path / "memory.sqlite3")
    response = create(client, [claim("SQLite WAL is required.", "decision", ["sqlite", "storage"])])
    assert response.status_code == 201
    claim_id = response.json()["created_ids"][0]
    hits = client.post("/v1/memories/search", headers=auth(), json={"project_key": PROJECT, "query": "SQLite WAL"}).json()["memories"]
    assert hits == [{"id": claim_id, "content": "SQLite WAL is required.", "type": "decision", "tags": ["sqlite", "storage"], "created_at": hits[0]["created_at"], "provenance": [{"session_id": "s1", "source_hash": "h1", "received_at": hits[0]["provenance"][0]["received_at"]}]}]


def test_duplicate_is_active_upsert_with_additional_source(tmp_path: Path):
    client = client_for(tmp_path / "memory.sqlite3")
    first = create(client, [claim("Use SQLite WAL.", tags=["db"])]).json()["created_ids"][0]
    second = create(client, [claim(" use  sqlite  wal. ", "procedure", ["durable"])], session="s2", source="h2")
    assert second.json() == {"created_ids": [], "updated_ids": [first]}
    hit = client.post("/v1/memories/search", headers=auth(), json={"project_key": PROJECT, "query": "sqlite"}).json()["memories"][0]
    assert hit["type"] == "procedure"
    assert hit["tags"] == ["db", "durable"]
    assert [(item["session_id"], item["source_hash"]) for item in hit["provenance"]] == [("s1", "h1"), ("s2", "h2")]


def test_fts_search_is_project_scoped_and_deterministic(tmp_path: Path):
    client = client_for(tmp_path / "memory.sqlite3")
    create(client, [claim("Use deterministic FTS5 ranking.")])
    create(client, [claim("FTS5 handles lexical matching.")])
    response = client.post("/v1/memories/search", headers=auth(), json={"project_key": PROJECT, "query": "FTS5"})
    assert [item["content"] for item in response.json()["memories"]] == ["Use deterministic FTS5 ranking.", "FTS5 handles lexical matching."]


def test_type_filtering_and_tombstones_are_excluded(tmp_path: Path):
    client = client_for(tmp_path / "memory.sqlite3")
    create(client, [claim("Use WAL for storage.", "decision"), claim("WAL improves concurrent storage.", "fact")])
    filtered = client.post("/v1/memories/search", headers=auth(), json={"project_key": PROJECT, "query": "WAL storage", "types": ["decision"]})
    assert [item["type"] for item in filtered.json()["memories"]] == ["decision"]
    claim_id = filtered.json()["memories"][0]["id"]
    assert client.delete(f"/v1/memories/{claim_id}", headers=auth()).status_code == 200
    all_hits = client.post("/v1/memories/search", headers=auth(), json={"project_key": PROJECT, "query": "WAL storage"}).json()["memories"]
    assert [item["type"] for item in all_hits] == ["fact"]
    state = client.get("/v1/memories/status", headers=auth(), params={"project_key": PROJECT}).json()
    assert state["active_count"] == 1 and state["tombstoned_count"] == 1 and state["database"]["journal_mode"] == "wal"


def test_auth_rejection_and_malformed_json(tmp_path: Path):
    client = client_for(tmp_path / "memory.sqlite3")
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.post("/v1/memories", headers={**auth(), "content-type": "application/json"}, content=b"{")
    assert response.status_code == 400


def test_restart_persistence(tmp_path: Path):
    database = tmp_path / "memory.sqlite3"
    first = client_for(database)
    create(first, [claim("Data survives process restarts.")])
    restarted = client_for(database)
    response = restarted.post("/v1/memories/search", headers=auth(), json={"project_key": PROJECT, "query": "survives"})
    assert [item["content"] for item in response.json()["memories"]] == ["Data survives process restarts."]
