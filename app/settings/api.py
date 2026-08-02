"""Settings REST surface (docs/FEATURES.md A1 / F2 / F4).

Exposes ``router`` for ``app/main.py`` to ``include_router()`` (wired by a
later step — this module does not touch main.py). Store writes are sync;
this layer wraps them with ``settings_write`` debug spans and hot-reloads
``app.state.config`` after a successful commit.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import ConfigError, GpuAssignment, load_config, reset_config_cache
from app.debug import new_trace, span
from app.settings.store import get_effective, update_model, update_routing

router = APIRouter(prefix="/api", tags=["settings"])


class ModelPatch(BaseModel):
    model_config = {"extra": "forbid"}

    gpu: GpuAssignment | None = None
    resident: bool | None = None
    ttl_s: int | None = None
    enabled: bool | None = None


class RoutingPatch(BaseModel):
    model_config = {"extra": "forbid"}

    rules: list[dict[str, Any]] | None = None
    intents: dict[str, str] | None = None
    classifier: dict[str, Any] | None = None


def _redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with any secret-bearing keys stripped.

    Phase-1 ``Config`` has no ``.env``-sourced fields; this is a forward
    hook so GET /api/settings never leaks keys once they exist.
    """
    return data


def _hot_reload(request: Request) -> None:
    reset_config_cache()
    request.app.state.config = load_config()


def _config_error_response(exc: ConfigError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"key_path": exc.key_path, "detail": exc.reason},
    )


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """Effective merged config as JSON (``class`` alias, secrets redacted)."""
    cfg = get_effective()
    dumped = cfg.model_dump(mode="json", by_alias=True)
    return _redact_secrets(dumped)


@router.put("/settings/models/{name}", response_model=None)
async def put_model(
    name: str, body: ModelPatch, request: Request
) -> dict[str, Any] | JSONResponse:
    """Patch one model's placement / residency / enable flags."""
    patch = body.model_dump(exclude_unset=True)
    try:
        async with span(
            new_trace(None), "settings_write", op="update_model", name=name
        ):
            cfg = update_model(name, patch)
            _hot_reload(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigError as exc:
        return _config_error_response(exc)

    entry = next(m for m in cfg.models if m.name == name)
    return entry.model_dump(mode="json", by_alias=True)


@router.put("/settings/routing", response_model=None)
async def put_routing(
    body: RoutingPatch, request: Request
) -> dict[str, Any] | JSONResponse:
    """Replace overlay routing rules and/or deep-merge intents."""
    patch = body.model_dump(exclude_unset=True)
    try:
        async with span(new_trace(None), "settings_write", op="update_routing"):
            cfg = update_routing(patch)
            _hot_reload(request)
    except ConfigError as exc:
        return _config_error_response(exc)

    return cfg.routing.model_dump(mode="json", by_alias=True)


@router.get("/models")
async def list_models() -> list[dict[str, Any]]:
    """Model-picker roster: alias, class, device, resident, loaded, ctx."""
    cfg = get_effective()
    return [
        {
            "alias": m.name,
            "class": m.class_,
            "device": m.gpu,
            "resident": m.resident,
            "loaded": False,
            "ctx": m.ctx,
        }
        for m in cfg.models
    ]
