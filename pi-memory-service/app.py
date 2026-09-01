# ruff: noqa: E501
"""Standalone Pi durable-memory service; it never calls an inference model."""

from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_REQUEST_BYTES = 131_072
WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Settings:
    database_path: Path
    token: str
    allowed_clients: frozenset[str]

    @classmethod
    def from_env(cls) -> Settings:
        token = os.environ.get("PI_MEMORY_TOKEN", "")
        if not token:
            raise RuntimeError("PI_MEMORY_TOKEN must be set through the environment file")
        allowed = os.environ.get("PI_MEMORY_ALLOWED_CLIENT", "192.168.0.246")
        clients = frozenset(item.strip() for item in allowed.split(",") if item.strip())
        if not clients:
            raise RuntimeError("PI_MEMORY_ALLOWED_CLIENT must contain at least one address")
        return cls(Path(os.environ.get("PI_MEMORY_DB", "./data/pi-memory.sqlite3")), token, clients)


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: Annotated[str, Field(min_length=1, max_length=2_000)]
    type: Literal["decision", "fact", "preference", "procedure", "lesson"]
    tags: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=20)] = []

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for tag in value:
            normalized = tag.casefold()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(tag)
        return result


class CreateMemoriesIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_key: Annotated[str, Field(min_length=1, max_length=2_048)]
    session_id: Annotated[str, Field(min_length=1, max_length=256)]
    source_hash: Annotated[str, Field(min_length=1, max_length=256)]
    claims: Annotated[list[ClaimIn], Field(min_length=1, max_length=50)]


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_key: Annotated[str, Field(min_length=1, max_length=2_048)]
    query: Annotated[str, Field(min_length=1, max_length=400)]
    types: Annotated[list[Literal["decision", "fact", "preference", "procedure", "lesson"]], Field(max_length=5)] = []
    limit: Annotated[int, Field(ge=1, le=20)] = 5


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def fts_query(value: str) -> str:
    words = WORD.findall(value.casefold())
    if not words:
        raise HTTPException(status_code=422, detail="query must include at least one word or number")
    if len(words) > 32:
        raise HTTPException(status_code=422, detail="query has too many search terms")
    return " AND ".join(f'"{word.replace(chr(34), chr(34) * 2)}"' for word in words)


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY,
                    project_key TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    content TEXT NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('decision','fact','preference','procedure','lesson')),
                    tags_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS claims_active_dedupe
                    ON claims(project_key, normalized_content) WHERE active = 1;
                CREATE TABLE IF NOT EXISTS claim_sources (
                    claim_id INTEGER NOT NULL REFERENCES claims(id),
                    session_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY(claim_id, session_id, source_hash)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(content, content='');
                """
            )

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert(self, request: CreateMemoriesIn) -> dict[str, list[int]]:
        created_ids: list[int] = []
        updated_ids: list[int] = []
        now = utc_now()
        with self.connection() as conn:
            for claim in request.claims:
                key = normalize(claim.content)
                row = conn.execute(
                    "SELECT id, tags_json FROM claims WHERE project_key = ? AND normalized_content = ? AND active = 1",
                    (request.project_key, key),
                ).fetchone()
                if row is None:
                    cursor = conn.execute(
                        """INSERT INTO claims(project_key, normalized_content, content, type, tags_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (request.project_key, key, claim.content, claim.type, json.dumps(claim.tags), now, now),
                    )
                    claim_id = int(cursor.lastrowid)
                    conn.execute("INSERT INTO claims_fts(rowid, content) VALUES (?, ?)", (claim_id, claim.content))
                    created_ids.append(claim_id)
                else:
                    claim_id = int(row["id"])
                    tags = list(dict.fromkeys(json.loads(row["tags_json"]) + claim.tags))
                    conn.execute(
                        "UPDATE claims SET type = ?, tags_json = ?, updated_at = ? WHERE id = ?",
                        (claim.type, json.dumps(tags), now, claim_id),
                    )
                    updated_ids.append(claim_id)
                conn.execute(
                    "INSERT OR IGNORE INTO claim_sources(claim_id, session_id, source_hash, received_at) VALUES (?, ?, ?, ?)",
                    (claim_id, request.session_id, request.source_hash, now),
                )
        return {"created_ids": created_ids, "updated_ids": updated_ids}

    def search(self, request: SearchIn) -> list[dict[str, object]]:
        type_sql = ""
        params: list[object] = [fts_query(request.query), request.project_key]
        if request.types:
            type_sql = f" AND c.type IN ({','.join('?' for _ in request.types)})"
            params.extend(request.types)
        params.append(request.limit)
        sql = f"""
            SELECT c.id, c.content, c.type, c.tags_json, c.created_at
            FROM claims_fts f JOIN claims c ON c.id = f.rowid
            WHERE claims_fts MATCH ? AND c.project_key = ? AND c.active = 1{type_sql}
            ORDER BY bm25(claims_fts), c.id ASC LIMIT ?
        """
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._result_row(conn, row) for row in rows]

    def _result_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
        provenance = [dict(source) for source in conn.execute(
            "SELECT session_id, source_hash, received_at FROM claim_sources WHERE claim_id = ? ORDER BY received_at, session_id",
            (row["id"],),
        )]
        return {"id": row["id"], "content": row["content"], "type": row["type"], "tags": json.loads(row["tags_json"]), "created_at": row["created_at"], "provenance": provenance}

    def tombstone(self, claim_id: int) -> bool:
        with self.connection() as conn:
            changed = conn.execute("UPDATE claims SET active = 0, updated_at = ? WHERE id = ? AND active = 1", (utc_now(), claim_id)).rowcount
            return changed == 1

    def status(self, project_key: str) -> dict[str, object]:
        with self.connection() as conn:
            active, tombstoned = conn.execute(
                "SELECT count(*) FILTER (WHERE active = 1), count(*) FILTER (WHERE active = 0) FROM claims WHERE project_key = ?",
                (project_key,),
            ).fetchone()
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        return {"project_key": project_key, "active_count": active, "tombstoned_count": tombstoned, "database": {"ok": True, "journal_mode": journal_mode}}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = MemoryStore(settings.database_path)
    app = FastAPI(title="pi-memory-service", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
                return JSONResponse({"detail": "request body is too large"}, status_code=413)
            if len(await request.body()) > MAX_REQUEST_BYTES:
                return JSONResponse({"detail": "request body is too large"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        code = 400 if any(error["type"] == "json_invalid" for error in exc.errors()) else 422
        return JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=code)

    def authorize(request: Request) -> None:
        client = request.client.host if request.client else ""
        if client not in settings.allowed_clients and client not in {"127.0.0.1", "::1"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="client is not allowed")
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, settings.token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/health")
    def health(_: None = Depends(authorize)):
        return {"ok": True}

    @app.post("/v1/memories", status_code=status.HTTP_201_CREATED)
    def create_memories(payload: CreateMemoriesIn, _: None = Depends(authorize)):
        return store.upsert(payload)

    @app.post("/v1/memories/search")
    def search_memories(payload: SearchIn, _: None = Depends(authorize)):
        return {"memories": store.search(payload)}

    @app.delete("/v1/memories/{claim_id}")
    def delete_memory(claim_id: Annotated[int, Field(gt=0)], _: None = Depends(authorize)):
        if not store.tombstone(claim_id):
            raise HTTPException(status_code=404, detail="active claim not found")
        return {"id": claim_id, "tombstoned": True}

    @app.get("/v1/memories/status")
    def memory_status(project_key: Annotated[str, Field(min_length=1, max_length=2_048)], _: None = Depends(authorize)):
        return store.status(project_key)

    return app
