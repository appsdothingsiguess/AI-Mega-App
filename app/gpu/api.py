"""GPU inventory and swapgen API (PLAN.md §4.1, docs/FEATURES.md F14).

Endpoints:
  GET  /api/gpu/inventory    — nvidia-smi → [{index, name, mem_total_mb, mem_free_mb}]
  GET  /api/gpu/swap-config  — current generated YAML as text/plain
  POST /api/gpu/apply        — write swap_yaml_path, poll llama-swap /health

Apply strategy (llama-swap v237 with -watch-config on the systemd unit):
  Write the new YAML to swap_yaml_path (atomic temp+rename, keep .bak).
  llama-swap picks up the change via its own -watch-config file watcher —
  there is no REST reload endpoint (/api/reload → 404 on v237).
  Poll GET <base>/health until body strips to "OK" (or timeout ~120 s).
  On poll failure: restore .bak and return HTTP 503.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.debug import new_trace, span
from app.config import get_config, reset_config_cache
from app.gpu.inventory import GPUInfo, fetch_inventory
from app.gpu.swapgen import generate
from app.warmup import all_residents_loaded, resident_swap_names, warmup_resident_models

router = APIRouter(prefix="/api/gpu", tags=["gpu"])

# Match llama-swap's own healthCheckTimeout (PLAN §4.1 sample = 120 s).
_POLL_TIMEOUT_S = 120
_POLL_INTERVAL_S = 2.0


def _health_url(base_url: str) -> str:
    """Derive llama-swap /health URL from llama_swap.base_url.

    base_url is e.g. "http://127.0.0.1:8080/v1"; health is at
    "http://127.0.0.1:8080/health".
    """
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/health"


def _config_for_apply(request: Request):
    """Return fresh disk config in production, injected config in tests."""
    if getattr(request.app.state, "reload_config_from_disk", False):
        reset_config_cache()
        config = get_config()
        request.app.state.config = config
        return config
    return request.app.state.config


async def _poll_health(url: str, timeout_s: float = _POLL_TIMEOUT_S) -> bool:
    """Poll GET url until body.strip() == 'OK' or timeout. Returns True on OK."""
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.text.strip() == "OK":
                    return True
            except httpx.HTTPError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(_POLL_INTERVAL_S, remaining))
    return False


@router.get("/inventory")
async def get_inventory(request: Request) -> list[dict]:
    """Return GPU inventory from nvidia-smi.  Returns [] on dev machines."""
    gpus = await fetch_inventory()
    return [asdict(g) for g in gpus]


@router.get("/swap-config", response_class=PlainTextResponse)
async def get_swap_config(request: Request) -> str:
    """Return the YAML that would be written by /apply (current config)."""
    config = request.app.state.config
    yaml_text = generate(config)
    return yaml_text


@router.post("/apply")
async def post_apply(request: Request) -> dict:
    """Write generated YAML to swap_yaml_path and wait for llama-swap to reload.

    llama-swap v237 reloads automatically via -watch-config (owner change,
    2026-07-30).  This endpoint writes the file then polls /health until OK.
    Rolls back the previous config (.bak) on poll timeout.
    """
    # Production app instances are created from the cached config at startup.
    # Reload here so the documented workflow is reliable: edit config.yaml,
    # POST /api/gpu/apply, and have that exact file state reach swapgen without
    # requiring a backend restart. Test apps inject a config directly and keep
    # that deterministic value.
    config = _config_for_apply(request)

    if not config.gpu.enabled:
        raise HTTPException(
            status_code=400,
            detail="gpu.enabled is false — swapgen writes are disabled; manage llama-swap.yaml manually.",
        )

    swap_path = Path(config.gpu.swap_yaml_path)
    bak_path = Path(str(swap_path) + ".bak")
    tmp_path = Path(str(swap_path) + ".tmp")

    trace_id = new_trace()
    async with span(trace_id, "swapgen") as sp:
        yaml_text = generate(config)
        sp.set(path=str(swap_path))

    # Back up existing file, write new YAML atomically.
    had_bak = False
    try:
        swap_path.parent.mkdir(parents=True, exist_ok=True)
        if swap_path.exists():
            import shutil
            shutil.copy2(swap_path, bak_path)
            had_bak = True
        tmp_path.write_text(yaml_text, encoding="utf-8")
        os.replace(tmp_path, swap_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write swap config: {exc}") from exc

    # Poll llama-swap health — it picks up the file via -watch-config.
    health_url = _health_url(config.llama_swap.base_url)
    ok = await _poll_health(health_url)

    if not ok:
        # Rollback: restore .bak if we kept one.
        if had_bak and bak_path.exists():
            try:
                os.replace(bak_path, swap_path)
            except OSError:
                pass
        raise HTTPException(
            status_code=503,
            detail=f"llama-swap did not return OK within {_POLL_TIMEOUT_S}s; rolled back to previous config.",
        )

    # llama-swap's /health only reports the proxy's own liveness, not any
    # individual model's — a reload kills every running llama-server process
    # (not just the swapping GPU0 slot), so every resident model is cold
    # right now even though the poll above just returned OK. Re-warm before
    # replying so the next real request (e.g. the classifier on the very
    # next chat turn) doesn't eat a cold-start timeout.
    # A single warmup pass right after /health goes OK can still race
    # llama-swap's process spawn (health checks the proxy, not each
    # llama-server), producing "group is shutting down" on the very models
    # we're trying to warm -- and unlike the startup loop, this endpoint
    # returns immediately after, with no periodic sweep to self-heal until
    # _WARMUP_INTERVAL_S later. Retry a few times so a transient race
    # doesn't leave residents cold for minutes after every apply.
    llm = getattr(request.app.state, "llm_client", None)
    residents = resident_swap_names(config)
    for attempt in range(3):
        await warmup_resident_models(llm, config)
        if await all_residents_loaded(llm, config):
            break
        if attempt < 2:
            await asyncio.sleep(3.0)
    else:
        if not residents:
            return {"ok": True, "health_url": health_url, "path": str(swap_path)}
        raise HTTPException(
            status_code=503,
            detail=(
                "llama-swap reloaded but resident models remain unloaded "
                "after warmup retries"
            ),
        )

    return {"ok": True, "health_url": health_url, "path": str(swap_path)}
