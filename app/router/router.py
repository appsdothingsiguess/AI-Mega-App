"""Three-layer smart router (PLAN.md §4.3, docs/FEATURES.md F5).

Strictly ordered:
  1. Manual override  — chat.model_override always wins
  2. Deterministic rules — attachment forcing then keyword rules (no model)
  3. Grammar-constrained classifier — LLMClient + json_schema

Every call emits one debug span (stage: "route") when trace_id is provided,
carrying: source, intent, model, confidence, latency_ms, layer, and
(on fallback) fallback_reason.

Routing NEVER raises into the chat path — every exception is caught and the
fallback model is returned.

Public interface (frozen — settings-api and eval import exactly this):
    async def route(chat, text, attachments, *, llm_client, config, trace_id) -> RouteResult

`chat` is duck-typed: accepts a sqlite3.Row (dict-like), a plain dict, or
any object with a .model_override attribute.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.config import Config, get_config
from app.debug import SpanHandle
from app.debug.trace import span
from app.llm_client import LLMClient
from app.router import classifier as _clf
from app.router import rules as _rules
from app.types import RouteResult

logger = logging.getLogger(__name__)

# Classes the classifier can emit — for fallback-defence only.
_VALID_CLASSES = frozenset(
    {"chat", "chit_chat", "code_task", "tool_call_needed", "reasoning_task", "vision_task"}
)


def _get_override(chat: object) -> str | None:
    """Extract model_override from a sqlite3.Row, dict, or plain object."""
    if isinstance(chat, dict):
        return chat.get("model_override")
    return getattr(chat, "model_override", None)


def _resolve_model(intent: str, cfg: Config) -> str:
    """Map a routing intent to a model alias via config.routing.intents."""
    alias = getattr(cfg.routing.intents, intent, None)
    if alias:
        return alias
    logger.warning("router: unknown intent %r, using fallback", intent)
    return cfg.routing.classifier.fallback_model


@asynccontextmanager
async def _maybe_span(trace_id: str | None) -> AsyncIterator[SpanHandle]:
    """Wrap work in a route span if trace_id is present; otherwise yield a
    no-op SpanHandle (fields accumulate but are never persisted)."""
    if trace_id:
        async with span(trace_id, "route") as sp:
            yield sp
    else:
        yield SpanHandle("", "route", {})


async def route(
    chat: object,
    text: str,
    attachments: list,
    *,
    llm_client: LLMClient | None = None,
    config: Config | None = None,
    trace_id: str | None = None,
) -> RouteResult:
    """Resolve model for one turn through three strictly ordered layers.

    Falls back to config.routing.classifier.fallback_model on any classifier
    error; never raises.  If trace_id is provided, emits one route span
    (stage="route") with source, intent, model, confidence, latency_ms, and
    (when applicable) fallback_reason.
    """
    cfg = config or get_config()
    started = time.monotonic()

    async with _maybe_span(trace_id) as sp:
        try:
            result, extra = await _route_inner(chat, text, attachments, llm_client, cfg, started)
        except Exception as exc:
            logger.exception("router: unexpected error: %s", exc)
            latency_ms = (time.monotonic() - started) * 1000
            fallback = cfg.routing.classifier.fallback_model
            result = RouteResult(
                model=fallback,
                source="classifier",
                intent="chat",
                latency_ms=latency_ms,
                confidence=None,
            )
            extra = {"layer": "classifier", "fallback_reason": "error"}

        sp.set(
            source=result.source,
            intent=result.intent,
            model=result.model,
            confidence=result.confidence,
            latency_ms=result.latency_ms,
            **extra,
        )

    return result


async def _route_inner(
    chat: object,
    text: str,
    attachments: list,
    llm_client: LLMClient | None,
    cfg: Config,
    started: float,
) -> tuple[RouteResult, dict[str, Any]]:
    """Inner pipeline — returns (RouteResult, extra_span_fields)."""

    def elapsed() -> float:
        return (time.monotonic() - started) * 1000

    # --- Layer 1: manual override ---
    override = _get_override(chat)
    if override:
        return (
            RouteResult(
                model=override,
                source="override",
                intent="chat",
                latency_ms=elapsed(),
                confidence=None,
            ),
            {"layer": "override"},
        )

    # --- Layer 2: deterministic rules ---
    intent = _rules.match(text, attachments, cfg.routing)
    if intent:
        model = _resolve_model(intent, cfg)
        return (
            RouteResult(
                model=model,
                source="rule",
                intent=intent,
                latency_ms=elapsed(),
                confidence=None,
            ),
            {"layer": "rule"},
        )

    # --- Layer 3: grammar-constrained classifier ---
    fallback_model = cfg.routing.classifier.fallback_model

    if llm_client is None:
        logger.warning("router: no llm_client for classifier, using fallback")
        return _fallback_result(fallback_model, elapsed(), "error")

    clf_cfg = cfg.routing.classifier
    clf_result = await _clf.classify(text, llm_client=llm_client, cfg=clf_cfg)

    if clf_result is None:
        return _fallback_result(fallback_model, elapsed(), "timeout")

    cls, conf = clf_result

    if cls not in _VALID_CLASSES:
        logger.warning("router: classifier returned unknown class %r", cls)
        return _fallback_result(fallback_model, elapsed(), "timeout")

    if conf < clf_cfg.confidence_threshold:
        # Degrade to fallback but preserve the actual confidence value and class
        return (
            RouteResult(
                model=fallback_model,
                source="classifier",
                intent=cls,
                latency_ms=elapsed(),
                confidence=conf,
            ),
            {"layer": "classifier", "fallback_reason": "low_confidence"},
        )

    model = _resolve_model(cls, cfg)
    return (
        RouteResult(
            model=model,
            source="classifier",
            intent=cls,
            latency_ms=elapsed(),
            confidence=conf,
        ),
        {"layer": "classifier"},
    )


def _fallback_result(
    fallback_model: str, latency_ms: float, reason: str
) -> tuple[RouteResult, dict[str, Any]]:
    return (
        RouteResult(
            model=fallback_model,
            source="classifier",
            intent="chat",
            latency_ms=latency_ms,
            confidence=None,
        ),
        {"layer": "classifier", "fallback_reason": reason},
    )
