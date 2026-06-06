"""Startup config validation across settings, litellm_config.yaml, and services."""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from app.config import Settings


def _parse_litellm_config(path: str | Path) -> set[str]:
    config_path = Path(path)
    if not config_path.exists():
        return set()
    with config_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return {
        entry["model_name"]
        for entry in data.get("model_list", [])
        if isinstance(entry, dict) and "model_name" in entry
    }


async def validate_config(settings: Settings) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for configuration consistency and reachability."""
    errors: list[str] = []
    warnings: list[str] = []

    litellm_models = _parse_litellm_config(settings.litellm_config_path)
    for intent, alias in settings.models.items():
        if alias not in litellm_models:
            errors.append(
                f"settings.models.{intent} = '{alias}' but no matching "
                f"model_name in litellm_config.yaml"
            )

    for alias in settings.models.values():
        if alias.startswith("local/") and alias not in settings.ollama_model_names:
            errors.append(
                f"Alias '{alias}' has no entry in settings.ollama_model_names"
            )

    local_aliases = [alias for alias in settings.models.values() if alias.startswith("local/")]
    if local_aliases:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{settings.ollama.base_url.rstrip('/')}/api/tags")
                resp.raise_for_status()
        except Exception as exc:
            errors.append(f"Ollama unreachable at {settings.ollama.base_url}: {exc}")

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{settings.qdrant.url.rstrip('/')}/collections")
            resp.raise_for_status()
    except Exception as exc:
        warnings.append(
            f"Qdrant unreachable at {settings.qdrant.url}: {exc}. "
            f"RAG retrieval will be skipped until Qdrant is available."
        )

    return errors, warnings
