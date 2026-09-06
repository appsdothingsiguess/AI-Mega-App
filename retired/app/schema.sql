-- Deterministic hand-written SQL (PLAN.md §6 guardrail) — no ORM.
-- Phase-1 subset only: chats, messages, traces, spans, settings_overlay
-- (docs/FEATURES.md A2 "Phase-1 subset"). `chats.summary` ships from day
-- one on purpose — Phase 2's rolling summarizer needs exactly one column,
-- and discovering that mid-wave would stall an agent on a frozen-schema
-- approval. No separate chat_summaries table.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS chats (
    id             TEXT PRIMARY KEY,
    title          TEXT,
    project_id     TEXT,
    model_override TEXT,
    summary        TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id),
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    model      TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);

CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    chat_id    TEXT,
    started_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_chat_id ON traces(chat_id);

CREATE TABLE IF NOT EXISTS spans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   TEXT NOT NULL REFERENCES traces(trace_id),
    stage      TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    data       TEXT
);

CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);

CREATE TABLE IF NOT EXISTS settings_overlay (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
