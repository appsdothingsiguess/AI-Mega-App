"""Keep ``litellm_config.yaml`` in sync with ``settings.json`` Ollama tags.

For every ``local/*`` alias in ``ollama_model_names`` that has a resolved
Ollama tag, upsert a matching ``model_list`` entry so LiteLLM can route to it.
Aliases without a resolved tag (empty placeholders) are left untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"model_list": []}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise
    if not isinstance(raw, dict):
        return {"model_list": []}
    raw.setdefault("model_list", [])
    if not isinstance(raw["model_list"], list):
        raw["model_list"] = []
    return raw


def sync_litellm_config(
    ollama_model_names: dict[str, str],
    base_url: str,
    path: str | Path,
    keep_alive: int | None = None,
) -> None:
    """Upsert ``local/*`` alias entries into the LiteLLM ``model_list``."""
    config_path = Path(path)
    config = _load_config(config_path)
    model_list: list[dict[str, Any]] = config["model_list"]

    by_name: dict[str, dict[str, Any]] = {
        entry.get("model_name"): entry
        for entry in model_list
        if isinstance(entry, dict) and entry.get("model_name")
    }

    for alias, tag in ollama_model_names.items():
        if not alias.startswith("local/"):
            continue
        if not tag or not tag.strip():
            continue
        entry = by_name.get(alias)
        if entry is None:
            entry = {"model_name": alias, "litellm_params": {}}
            model_list.append(entry)
            by_name[alias] = entry
        litellm_params = entry.setdefault("litellm_params", {})
        litellm_params["model"] = f"ollama_chat/{tag}"
        litellm_params["api_base"] = base_url
        if keep_alive is not None:
            litellm_params["keep_alive"] = keep_alive

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


__all__ = ["sync_litellm_config"]
