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
    _merge_overlay,
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
    # Use the public loader path so sparse-model merge and cross-field
    # reference validation are exactly the same for reads and writes.
    base = _read_yaml(CONFIG_PATH)
    merged = _merge_overlay(base, overlay)
    try:
        config = Config.model_validate(merged)
    except ValidationError as exc:
        from app.config import _first_error_key_path

        key_path, reason = _first_error_key_path(exc)
        raise ConfigError(key_path, reason) from exc
    # Temporarily validate through load_config's shared reference checker
    # without touching disk. Kept as an import-local to avoid widening the
    # public configuration API solely for the settings writer.
    from app.config import _validate_model_references

    _validate_model_references(config)
    return config


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

    New overlays store only the changed fields under ``models.<alias>``.
    Legacy full-roster overlays are read compatibly and migrated to sparse
    patches on the next model write.
    Raises ``KeyError`` if ``name`` is missing; ``ConfigError`` if the
    merged result fails validation (overlay left unchanged).
    """
    effective = get_effective()
    if not any(model.name == name for model in effective.models):
        raise KeyError(f"unknown model: {name!r}")

    applied = {k: v for k, v in patch.items() if k in _MODEL_PATCH_KEYS}
    overlay = dict(read_overlay())
    model_patches = overlay.get("models", {})
    if isinstance(model_patches, list):
        base_models = {
            model["name"]: model for model in _read_yaml(CONFIG_PATH).get("models", [])
        }
        sparse: dict[str, dict[str, Any]] = {}
        for legacy in model_patches:
            if not isinstance(legacy, dict) or not isinstance(legacy.get("name"), str):
                continue
            base = base_models.get(legacy["name"])
            if base is None:
                continue
            delta = {
                key: value for key, value in legacy.items()
                if key != "name" and base.get(key) != value
            }
            if delta:
                sparse[legacy["name"]] = delta
        model_patches = sparse
    elif not isinstance(model_patches, dict):
        raise ConfigError("models", "overlay models must be a mapping or list")

    sparse = {alias: dict(values) for alias, values in model_patches.items()}
    sparse[name] = {**sparse.get(name, {}), **applied}
    overlay["models"] = sparse
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
