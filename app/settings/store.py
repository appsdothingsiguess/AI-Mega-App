"""Settings overlay store — sole writer of ``settings.local.yaml``.

Never touches ``config.yaml``. Validates the deep-merged result through
``Config`` before committing; failed validation leaves the overlay file
unchanged and raises ``ConfigError``.

Debug spans: this module stays synchronous. Callers (``api.py``) should
wrap writes with ``async with span(new_trace(None), "settings_write", ...)``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.config import (
    CONFIG_PATH,
    OVERLAY_PATH,
    Config,
    ConfigError,
    _deep_merge,
    _first_error_key_path,
    _read_yaml,
    load_config,
    reset_config_cache,
)

_MODEL_PATCH_KEYS = frozenset({"gpu", "resident", "ttl_s", "enabled"})


def read_overlay() -> dict[str, Any]:
    """Return the raw overlay dict (empty if the file is absent)."""
    return _read_yaml(OVERLAY_PATH)


def get_effective() -> Config:
    """Return the merged, validated config (base + overlay)."""
    return load_config()


def _validate_merged(overlay: dict[str, Any]) -> Config:
    base = _read_yaml(CONFIG_PATH)
    merged = _deep_merge(base, overlay)
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        key_path, reason = _first_error_key_path(exc)
        raise ConfigError(key_path, reason) from exc


def _write_atomic(data: dict[str, Any]) -> None:
    path = OVERLAY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    text = yaml.safe_dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def update_model(name: str, patch: dict[str, Any]) -> Config:
    """Patch one model's ``gpu`` / ``resident`` / ``ttl_s`` / ``enabled``.

    Overlay ``models`` is replaced wholesale with the patched full roster.
    Raises ``KeyError`` if ``name`` is missing; ``ConfigError`` if the
    merged result fails validation (overlay left unchanged).
    """
    effective = get_effective()
    roster = [m.model_dump(by_alias=True) for m in effective.models]
    idx = next((i for i, m in enumerate(roster) if m.get("name") == name), None)
    if idx is None:
        raise KeyError(f"unknown model: {name!r}")

    applied = {k: v for k, v in patch.items() if k in _MODEL_PATCH_KEYS}
    roster[idx] = {**roster[idx], **applied}

    overlay = {**read_overlay(), "models": roster}
    validated = _validate_merged(overlay)
    _write_atomic(overlay)
    reset_config_cache()
    return validated


def update_routing(body: dict[str, Any]) -> Config:
    """Replace overlay ``routing.rules``; deep-merge ``routing.intents`` and
    ``routing.classifier``.

    ``body`` may include ``rules``, ``intents``, and/or ``classifier``.
    Raises ``ConfigError`` on validation failure (overlay left unchanged).
    """
    overlay = dict(read_overlay())
    routing = dict(overlay.get("routing") or {})

    if "rules" in body:
        routing["rules"] = body["rules"]
    if "intents" in body:
        existing = dict(routing.get("intents") or {})
        routing["intents"] = _deep_merge(existing, dict(body["intents"]))
    if "classifier" in body:
        existing = dict(routing.get("classifier") or {})
        routing["classifier"] = _deep_merge(existing, dict(body["classifier"]))

    overlay["routing"] = routing
    validated = _validate_merged(overlay)
    _write_atomic(overlay)
    reset_config_cache()
    return validated
