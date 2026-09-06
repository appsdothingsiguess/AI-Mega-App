"""Resident-model warm-up (PLAN.md §4.1). Shared by app/main.py (startup +
periodic sweep) and app/gpu/api.py (immediately after a config apply, since
llama-swap's own -watch-config reload kills every running llama-server
process, not just the swapping GPU0 slot -- resident CPU/GPU1 models sit
cold until something pings them).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Config, ModelEntry

logger = logging.getLogger(__name__)


ServerIdentity = tuple[str, int | str]


def server_identity(model: ModelEntry) -> ServerIdentity:
    """Return llama-swap's physical server identity for a model entry."""
    return (model.file, model.gpu)


def resident_swap_names(cfg: Config) -> list[str]:
    """Enabled resident CPU/GPU1 server names, deduped by ``(file, gpu)``.

    Filtering happens before deduplication.  A GPU0 alias sharing a GGUF
    with a CPU or GPU1 resident therefore cannot hide that resident server,
    and the same GGUF on CPU and GPU1 remains two independent warmup targets.
    Generic warmup deliberately excludes every GPU0 entry: pinging the
    default or a swap member would replace the active ``--parallel 1`` KV
    prefix.
    """
    names: list[str] = []
    seen: set[ServerIdentity] = set()
    for model in cfg.models:
        if not model.enabled or not model.resident or model.gpu == 0:
            continue
        identity = server_identity(model)
        if identity in seen:
            continue
        seen.add(identity)
        names.append(model.name)
    return names


_WARMUP_TIMEOUT_S = 60.0
_STARTUP_BACKOFF_S = 15.0  # retry interval until all residents report loaded


async def warmup_one(
    llm: Any, model: str, *, is_embed: bool = False, timeout_s: float = _WARMUP_TIMEOUT_S,
) -> None:
    logger.info("warm-up starting (%s)", model)
    try:
        async with asyncio.timeout(timeout_s):
            if is_embed:
                await llm.embed(model, ["ping"])
            else:
                async for _ in llm.chat(
                    model, [{"role": "user", "content": "ping"}],
                    max_tokens=1, stream=False, thinking=False,
                ):
                    pass
        logger.info("warm-up complete (%s)", model)
    except TimeoutError:
        logger.warning("warm-up timed out after %.0fs (%s)", timeout_s, model)
    except Exception as exc:
        logger.warning("warm-up failed (%s): %s", model, exc)


async def warmup_resident_models(
    llm: Any, cfg: Config, *, timeout_s: float = _WARMUP_TIMEOUT_S,
    skip: set[str] | None = None,
) -> None:
    """Ping every resident CPU/GPU1 server once so llama-swap loads it, in
    parallel so one slow cold start doesn't delay the others.

    ``skip`` excludes names already confirmed loaded (see
    ``loaded_resident_names``) — re-pinging an already-loaded model sends a
    real inference completion that competes with live traffic for no
    benefit, which matters here because the startup retry loop below calls
    this every _STARTUP_BACKOFF_S until convergence.
    """
    if llm is None or cfg is None:
        return
    embed_names = {m.name for m in cfg.models if m.class_ == "embed"}
    names = [n for n in resident_swap_names(cfg) if n not in (skip or set())]
    if not names:
        return
    await asyncio.gather(
        *(
            warmup_one(llm, name, is_embed=name in embed_names, timeout_s=timeout_s)
            for name in names
        )
    )


async def loaded_resident_names(llm: Any, cfg: Config) -> set[str]:
    """Resident swap names llama-swap currently reports as loaded.

    Best-effort: returns an empty set (never raises) if the status query
    fails, so callers fall back to warming up everything.
    """
    if llm is None or cfg is None:
        return set()
    names = resident_swap_names(cfg)
    try:
        status = await llm.model_status()
    except Exception:
        return set()
    return {n for n in names if status.get(n, False)}


async def all_residents_loaded(llm: Any, cfg: Config) -> bool:
    """Return True once every resident swap name reports loaded via
    llama-swap's /v1/models status.  Safe to call when llm/cfg is None
    (returns False) so the retry loop in _warmup_loop doesn't crash."""
    if llm is None or cfg is None:
        return False
    names = resident_swap_names(cfg)
    if not names:
        return True  # vacuously true — nothing to load
    try:
        status = await llm.model_status()
    except Exception:
        logger.warning("warmup: model_status failed, will retry")
        return False
    all_ok = all(status.get(name, False) for name in names)
    if all_ok:
        logger.info("warmup: all %d resident models loaded", len(names))
    else:
        missing = [name for name in names if not status.get(name, False)]
        logger.info(
            "warmup: %d/%d residents loaded, retrying: %s",
            len(names) - len(missing),
            len(names),
            missing,
        )
    return all_ok
