#!/usr/bin/env python3
"""Standalone classifier eval harness — talks directly to Ollama (no FastAPI).

Usage (from repo root):
  python scripts/eval_classifier.py --list-tags
  python scripts/eval_classifier.py --smoke --variant local
  python scripts/eval_classifier.py --smoke --variant remote
  python scripts/eval_classifier.py --full --variant local --models qwen2.5:1.5b-32k
  python scripts/eval_classifier.py --full --variant remote --models qwen2.5:1.5b-32k
  python scripts/eval_classifier.py --smoke --prompt-file path/to/prompt.txt --min-accuracy 0.85

--variant local  → prompts/local.txt  + gold_map.json
                   (all local/* tier aliases; general_chat → local/qwen3-8b)
--variant remote → prompts/remote.txt + gold_map.remote.json
                   (general_chat/coding_advanced → remote/deepseek-v4-pro;
                    web_search/deep_research → remote/kimi-k2-6;
                    all other intents keep local/* tier aliases)

Do not run two variants concurrently against the same CPU classifier (num_gpu:0).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.classifier_qwen import CLASSIFIER_OLLAMA_OPTIONS  # noqa: E402
from app.config import DEFAULT_CLASSIFIER_PROMPT, Settings, get_settings  # noqa: E402

EVAL_DIR = REPO_ROOT / "eval" / "classifier"
PROGRESS_EVERY = 25
GOLD_MAP_PATH = EVAL_DIR / "gold_map.json"
GOLD_MAP_REMOTE_PATH = EVAL_DIR / "gold_map.remote.json"
PROMPT_LOCAL_PATH = EVAL_DIR / "prompts" / "local.txt"
PROMPT_REMOTE_PATH = EVAL_DIR / "prompts" / "remote.txt"
DATASET_PATH = EVAL_DIR / "dataset.jsonl"
RUNS_DIR = EVAL_DIR / "runs"
TIER_SUFFIX_RE = re.compile(r"-(light|medium|heavy)$")

# Only these intents have a real remote/* alias in litellm_config.yaml today
# (remote/deepseek-v4-pro, remote/kimi-k2-6). Every other intent has no remote
# equivalent, so both variants must keep the local tier alias for them.
REMOTE_ONLY_INTENTS = {"general_chat", "web_search", "deep_research", "coding_advanced"}

# Embedded smoke fallback (3 prompts × 11 intents) if dataset.jsonl is missing.
_EMBEDDED_SMOKE: list[dict[str, Any]] = [
    {"id": "gc-001", "message": "Explain what a REST API is in simple terms", "intent": "general_chat"},
    {"id": "gc-002", "message": "Draft a polite email declining a meeting invitation", "intent": "general_chat"},
    {"id": "gc-003", "message": "Open README.md and summarize what the project does", "intent": "general_chat"},
    {"id": "ws-001", "message": "What's the weather like today in Denver?", "intent": "web_search"},
    {"id": "ws-002", "message": "Look up the current CEO of OpenAI", "intent": "web_search"},
    {"id": "ws-003", "message": "Find the score of last night's Lakers game", "intent": "web_search"},
    {"id": "dr-001", "message": "Summarize recent papers on protein folding", "intent": "deep_research"},
    {"id": "dr-002", "message": "Literature review on federated learning privacy", "intent": "deep_research"},
    {"id": "dr-003", "message": "Compile a research brief synthesizing trends in edge AI chips", "intent": "deep_research"},
    {"id": "cb-001", "message": "Write a Python function that parses a CSV of users", "intent": "coding_basic"},
    {"id": "cb-002", "message": "Implement OAuth login boilerplate for a Flask app", "intent": "coding_basic"},
    {"id": "cb-003", "message": "Create a regex that matches email addresses", "intent": "coding_basic"},
    {"id": "ca-001", "message": "Fix this bug in my Python code: IndexError on line 42", "intent": "coding_advanced"},
    {"id": "ca-002", "message": "Add unit tests for this UserService class", "intent": "coding_advanced"},
    {"id": "ca-003", "message": "Refactor this function to remove the nested try/except", "intent": "coding_advanced"},
    {"id": "ba-001", "message": "Run git pull origin main", "intent": "bash"},
    {"id": "ba-002", "message": "Execute pytest -q in the project root", "intent": "bash"},
    {"id": "ba-003", "message": "Start the docker compose stack for this repo", "intent": "bash"},
    {"id": "pg-001", "message": "Convert markdown README to PDF", "intent": "pdf_gen"},
    {"id": "pg-002", "message": "Make me a PDF research report on AI safety", "intent": "pdf_gen"},
    {"id": "pg-003", "message": "Convert these images into a single PDF", "intent": "pdf_gen"},
    {"id": "fo-001", "message": "Find the invoice PDF in my downloads folder", "intent": "file_ops"},
    {"id": "fo-002", "message": "Open package.json and show the dependencies", "intent": "file_ops"},
    {"id": "fo-003", "message": "Search my codebase for TODO comments", "intent": "file_ops"},
    {"id": "vi-001", "message": "Classify the document type from this image", "intent": "vision"},
    {"id": "vi-002", "message": "Read the labels in this chart screenshot", "intent": "vision"},
    {"id": "vi-003", "message": "Describe what is shown in this attached photo", "intent": "vision"},
    {"id": "rm-001", "message": "Think through this logic puzzle step by step", "intent": "reasoning_medium"},
    {"id": "rm-002", "message": "Solve this puzzle about three switches and three bulbs", "intent": "reasoning_medium"},
    {"id": "rm-003", "message": "Plan how to schedule these conflicting meeting constraints", "intent": "reasoning_medium"},
    {"id": "rh-001", "message": "Do a root cause analysis of this multi-system failure", "intent": "reasoning_heavy"},
    {"id": "rh-002", "message": "Deep reasoning on a complex multi-step plan with tradeoffs", "intent": "reasoning_heavy"},
    {"id": "rh-003", "message": "Expert-level analysis of cascading dependency failures across services", "intent": "reasoning_heavy"},
]


@dataclass
class GoldRow:
    id: str
    message: str
    intent: str
    tools: list[str]
    model: str
    tier: str
    notes: str = ""
    source: str = "hand"


@dataclass
class PredRow:
    intent: str
    tools: list[str]
    model: str
    tier: str
    raw: str = ""
    parse_ok: bool = True


@dataclass
class DetailRow:
    id: str
    message: str
    classifier_model: str
    expected: dict[str, Any]
    predicted: dict[str, Any]
    intent_ok: bool
    tools_ok: bool
    model_ok: bool
    tier_ok: bool
    composite_ok: bool
    latency_ms: float
    raw_response: str = ""


@dataclass
class ModelSummary:
    classifier_model: str
    n: int
    composite: float
    intent_acc: float
    tools_acc: float
    model_acc: float
    tier_acc: float
    latency_mean_ms: float = 0.0
    latency_median_ms: float = 0.0
    intent_confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    model_confusion: dict[str, dict[str, int]] = field(default_factory=dict)


def load_gold_map(path: Path = GOLD_MAP_PATH) -> dict[str, dict[str, Any]]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit(f"Invalid gold map (not an object): {path}")
        return data
    # Fallback matching plan canonical table
    return {
        "general_chat": {"tools": [], "model": "local/qwen3-8b", "tier": "n/a"},
        "web_search": {"tools": ["web_search"], "model": "local/tool-calling-medium", "tier": "medium"},
        "deep_research": {"tools": ["web_search"], "model": "local/reasoning-heavy", "tier": "heavy"},
        "coding_basic": {"tools": [], "model": "local/coding-light", "tier": "light"},
        "coding_advanced": {"tools": [], "model": "local/coding-heavy", "tier": "heavy"},
        "bash": {"tools": ["bash"], "model": "local/tool-calling-medium", "tier": "medium"},
        "pdf_gen": {"tools": ["pdf_gen"], "model": "local/tool-calling-medium", "tier": "medium"},
        "file_ops": {"tools": ["file_ops"], "model": "local/tool-calling-medium", "tier": "medium"},
        "vision": {"tools": ["vision"], "model": "local/vision-medium", "tier": "medium"},
        "reasoning_medium": {
            "tools": ["web_search", "bash", "pdf_gen", "file_ops"],
            "model": "local/reasoning-medium",
            "tier": "medium",
        },
        "reasoning_heavy": {
            "tools": ["web_search", "bash", "pdf_gen", "file_ops"],
            "model": "local/reasoning-heavy",
            "tier": "heavy",
        },
    }


def tier_from_model(model: str) -> str:
    m = TIER_SUFFIX_RE.search(model or "")
    return m.group(1) if m else "n/a"


def enrich_row(raw: dict[str, Any], gold_map: dict[str, dict[str, Any]]) -> GoldRow:
    """Attach expected tools/model/tier from `gold_map` for this row's intent.

    `tools` are variant-independent (same tool set regardless of local vs remote
    model choice) so the dataset's stored `tools` are validated against the map.
    `model`/`tier` are variant-dependent, so they are always taken from the
    active gold_map rather than the dataset file — the dataset stores only one
    (local) baseline for readability, not a hard constraint.
    """
    intent = str(raw["intent"])
    if intent not in gold_map:
        raise SystemExit(f"Unknown intent in dataset row {raw.get('id')}: {intent}")
    expected = gold_map[intent]
    tools = raw.get("tools", expected["tools"])
    if set(tools) != set(expected["tools"]):
        raise SystemExit(
            f"Gold tools mismatch for {raw.get('id')} intent={intent}: "
            f"got tools={tools}; expected {expected['tools']}"
        )
    return GoldRow(
        id=str(raw["id"]),
        message=str(raw["message"]),
        intent=intent,
        tools=list(expected["tools"]),
        model=str(expected["model"]),
        tier=str(expected["tier"]),
        notes=str(raw.get("notes", "")),
        source=str(raw.get("source", "hand")),
    )


def load_dataset(mode: str, gold_map: dict[str, dict[str, Any]]) -> list[GoldRow]:
    rows: list[dict[str, Any]]
    if DATASET_PATH.exists():
        rows = []
        for line_no, line in enumerate(DATASET_PATH.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Bad JSONL at {DATASET_PATH}:{line_no}: {exc}") from exc
    else:
        rows = [dict(r) for r in _EMBEDDED_SMOKE]

    gold = [enrich_row(r, gold_map) for r in rows]
    if mode == "smoke":
        by_intent: dict[str, list[GoldRow]] = defaultdict(list)
        for row in gold:
            by_intent[row.intent].append(row)
        smoke: list[GoldRow] = []
        for intent in sorted(gold_map.keys()):
            picked = by_intent.get(intent, [])[:3]
            if len(picked) < 3:
                raise SystemExit(
                    f"Smoke needs ≥3 prompts for intent={intent}, found {len(picked)}"
                )
            smoke.extend(picked)
        return smoke
    return gold


def ollama_model_name(classifier: str) -> str:
    if classifier.startswith("ollama/"):
        return classifier.removeprefix("ollama/")
    return classifier


def extract_json(response_text: str) -> str:
    """Match classifier_qwen.QwenClassifierAdapter._extract_json."""
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_classifier_json(response_text: str) -> PredRow:
    try:
        data = json.loads(extract_json(response_text))
    except json.JSONDecodeError:
        return PredRow(intent="general_chat", tools=[], model="", tier="n/a", raw=response_text, parse_ok=False)
    if not isinstance(data, dict):
        return PredRow(intent="general_chat", tools=[], model="", tier="n/a", raw=response_text, parse_ok=False)

    intent = data.get("intent")
    tools = data.get("tools", [])
    model = data.get("model", "")
    if not isinstance(intent, str) or not isinstance(tools, list):
        return PredRow(intent="general_chat", tools=[], model="", tier="n/a", raw=response_text, parse_ok=False)
    if not all(isinstance(t, str) for t in tools):
        return PredRow(intent="general_chat", tools=[], model="", tier="n/a", raw=response_text, parse_ok=False)
    if not isinstance(model, str):
        model = str(model) if model is not None else ""

    return PredRow(
        intent=intent,
        tools=list(tools),
        model=model,
        tier=tier_from_model(model),
        raw=response_text,
        parse_ok=True,
    )


def list_ollama_tags(
    base_url: str, timeout_s: float, client: httpx.Client | None = None
) -> list[str]:
    url = f"{base_url.rstrip('/')}/api/tags"
    own = client is None
    if own:
        client = httpx.Client(timeout=timeout_s)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        models = resp.json().get("models") or []
    finally:
        if own:
            client.close()
    names: list[str] = []
    for m in models:
        if isinstance(m, dict) and isinstance(m.get("name"), str):
            names.append(m["name"])
    return sorted(names)


def warm_classifier(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    keep_alive: int,
) -> None:
    """Empty generate with live options so Ollama keeps the CPU classifier resident."""
    payload = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": keep_alive,
        "options": dict(CLASSIFIER_OLLAMA_OPTIONS),
    }
    resp = client.post(f"{base_url.rstrip('/')}/api/generate", json=payload)
    resp.raise_for_status()
    print(
        f"Warmed classifier {model} (num_gpu={CLASSIFIER_OLLAMA_OPTIONS['num_gpu']})",
        flush=True,
    )


def pick_classifier_models(
    tags: list[str],
    configured: str,
    explicit: list[str] | None,
    max_models: int = 3,
) -> list[str]:
    if explicit:
        return explicit[:max_models]

    current = ollama_model_name(configured)
    preferred_substrings = ("qwen2.5:1.5b", "qwen2.5:3b", "qwen3:1.7b", "qwen3:4b", "1.5b", "3b")
    candidates: list[str] = []
    if current:
        candidates.append(current)
    for tag in tags:
        base = tag.split(":")[0] if ":" in tag else tag
        if any(s in tag.lower() or s in base.lower() for s in preferred_substrings):
            if tag not in candidates and ollama_model_name(tag) not in {
                ollama_model_name(c) for c in candidates
            }:
                candidates.append(tag)
        if len(candidates) >= max_models:
            break
    if len(candidates) < max_models:
        for tag in tags:
            if tag not in candidates:
                candidates.append(tag)
            if len(candidates) >= max_models:
                break
    return candidates[:max_models]


def classify_message(
    client: httpx.Client,
    *,
    base_url: str,
    model: str,
    system_prompt: str,
    message: str,
    keep_alive: int,
) -> tuple[PredRow, float]:
    """Mirror app/adapters/classifier_qwen.py payload (CLASSIFIER_OLLAMA_OPTIONS)."""
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": message,
        "stream": False,
        "keep_alive": keep_alive,
        "options": dict(CLASSIFIER_OLLAMA_OPTIONS),
    }
    started = time.perf_counter()
    url = f"{base_url.rstrip('/')}/api/generate"
    try:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        response_text = str(resp.json().get("response", ""))
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        return (
            PredRow(
                intent="general_chat",
                tools=[],
                model="",
                tier="n/a",
                raw=f"<error: {exc}>",
                parse_ok=False,
            ),
            latency_ms,
        )
    latency_ms = (time.perf_counter() - started) * 1000
    return parse_classifier_json(response_text), latency_ms


def tools_equal(a: list[str], b: list[str]) -> bool:
    return set(a) == set(b)


def score_row(gold: GoldRow, pred: PredRow, classifier_model: str, latency_ms: float) -> DetailRow:
    intent_ok = pred.intent == gold.intent
    tools_ok = tools_equal(pred.tools, gold.tools)
    model_ok = pred.model == gold.model
    tier_ok = pred.tier == gold.tier
    return DetailRow(
        id=gold.id,
        message=gold.message,
        classifier_model=classifier_model,
        expected={
            "intent": gold.intent,
            "tools": gold.tools,
            "model": gold.model,
            "tier": gold.tier,
        },
        predicted={
            "intent": pred.intent,
            "tools": pred.tools,
            "model": pred.model,
            "tier": pred.tier,
            "parse_ok": pred.parse_ok,
        },
        intent_ok=intent_ok,
        tools_ok=tools_ok,
        model_ok=model_ok,
        tier_ok=tier_ok,
        composite_ok=intent_ok and tools_ok and model_ok and tier_ok,
        latency_ms=round(latency_ms, 2),
        raw_response=pred.raw,
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summarize(details: list[DetailRow], classifier_model: str) -> ModelSummary:
    n = len(details) or 1
    intent_conf: dict[str, Counter[str]] = defaultdict(Counter)
    model_conf: dict[str, Counter[str]] = defaultdict(Counter)
    latencies = [d.latency_ms for d in details]
    for d in details:
        intent_conf[d.expected["intent"]][d.predicted["intent"]] += 1
        model_conf[d.expected["model"]][d.predicted["model"] or "<empty>"] += 1
    return ModelSummary(
        classifier_model=classifier_model,
        n=len(details),
        composite=sum(1 for d in details if d.composite_ok) / n,
        intent_acc=sum(1 for d in details if d.intent_ok) / n,
        tools_acc=sum(1 for d in details if d.tools_ok) / n,
        model_acc=sum(1 for d in details if d.model_ok) / n,
        tier_acc=sum(1 for d in details if d.tier_ok) / n,
        latency_mean_ms=round(sum(latencies) / n, 2) if details else 0.0,
        latency_median_ms=round(_median(latencies), 2),
        intent_confusion={k: dict(v) for k, v in intent_conf.items()},
        model_confusion={k: dict(v) for k, v in model_conf.items()},
    )


def write_run_artifacts(
    run_dir: Path,
    *,
    prompt: str,
    mode: str,
    variant: str,
    gold_map_name: str,
    settings: Settings,
    summaries: list[ModelSummary],
    details: list[DetailRow],
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    with (run_dir / "details.jsonl").open("w", encoding="utf-8") as fh:
        for d in details:
            fh.write(json.dumps(asdict(d), ensure_ascii=False) + "\n")

    best = max(summaries, key=lambda s: s.composite) if summaries else None
    summary = {
        "timestamp": run_dir.name,
        "mode": mode,
        "variant": variant,
        "gold_map": gold_map_name,
        "ollama_base_url": settings.ollama.base_url,
        "configured_classifier": settings.router.classifier,
        "models": [asdict(s) for s in summaries],
        "best": asdict(best) if best else None,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def print_summary(summaries: list[ModelSummary], run_dir: Path) -> None:
    print(f"\nRun dir: {run_dir}", flush=True)
    for s in summaries:
        print(
            f"  {s.classifier_model}: n={s.n} "
            f"composite={s.composite:.1%} "
            f"intent={s.intent_acc:.1%} "
            f"tools={s.tools_acc:.1%} "
            f"model={s.model_acc:.1%} "
            f"tier={s.tier_acc:.1%} "
            f"latency_mean={s.latency_mean_ms:.0f}ms "
            f"latency_median={s.latency_median_ms:.0f}ms",
            flush=True,
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Eval classifier prompts against Ollama")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true", help="3 prompts × 11 intents (default)")
    mode.add_argument("--full", action="store_true", help="Full dataset.jsonl suite")
    p.add_argument("--list-tags", action="store_true", help="List Ollama /api/tags and exit")
    p.add_argument(
        "--models",
        type=str,
        default="",
        help="Comma-separated Ollama tags (max 3). Default: auto-pick including router.classifier",
    )
    p.add_argument(
        "--variant",
        choices=("local", "remote"),
        default="local",
        help=(
            "Which model-naming variant to test: 'local' expects tier aliases "
            "(local/coding-light, local/tool-calling-medium, ...) for every intent; "
            "'remote' expects remote/deepseek-v4-pro or remote/kimi-k2-6 for "
            "general_chat/web_search/deep_research/coding_advanced (the only "
            "intents with a real remote/* alias in litellm_config.yaml) and local "
            "tier aliases for everything else. Selects the matching default "
            "prompt (eval/classifier/prompts/local.txt or remote.txt) and gold "
            "map (gold_map.json or gold_map.remote.json) unless overridden."
        ),
    )
    p.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="Classifier system prompt file (default: eval/classifier/prompts/<variant>.txt, "
        "falling back to settings/router.classifier_prompt)",
    )
    p.add_argument(
        "--gold-map",
        type=Path,
        default=None,
        help="Gold map JSON override (default: gold_map.json or gold_map.remote.json per --variant)",
    )
    p.add_argument(
        "--min-accuracy",
        type=float,
        default=0.85,
        help="Exit non-zero if best smoke composite < this (default 0.85)",
    )
    p.add_argument(
        "--settings-json",
        type=Path,
        default=None,
        help="Override SETTINGS_JSON_PATH for this process",
    )
    p.add_argument(
        "--ids",
        type=str,
        default="",
        help="Comma-separated row ids to run (e.g. ws-001). Skips smoke/full size rules.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.settings_json is not None:
        import os

        os.environ["SETTINGS_JSON_PATH"] = str(args.settings_json.resolve())
        get_settings.cache_clear()

    settings = get_settings()
    base_url = settings.ollama.base_url
    timeout_s = settings.health.classifier_timeout_s or 30.0

    if args.list_tags:
        try:
            tags = list_ollama_tags(base_url, timeout_s)
        except httpx.HTTPError as exc:
            print(f"Failed to list tags at {base_url}/api/tags: {exc}", file=sys.stderr)
            return 2
        print(f"Ollama base_url: {base_url}")
        print(f"Configured classifier: {settings.router.classifier}")
        for tag in tags:
            mark = " *" if ollama_model_name(tag) == ollama_model_name(settings.router.classifier) else ""
            print(f"  {tag}{mark}")
        return 0

    mode = "full" if args.full else "smoke"
    gold_map_path = args.gold_map or (
        GOLD_MAP_REMOTE_PATH if args.variant == "remote" else GOLD_MAP_PATH
    )
    gold_map = load_gold_map(gold_map_path)
    id_filter = {x.strip() for x in args.ids.split(",") if x.strip()}
    # --ids always loads the full file (or embedded set) so any id is reachable.
    dataset = load_dataset("full" if id_filter else mode, gold_map)
    if id_filter:
        dataset = [r for r in dataset if r.id in id_filter]
        if not dataset:
            print(f"No rows matched --ids {sorted(id_filter)}", file=sys.stderr)
            return 2
        mode = "ids"

    default_prompt_path = PROMPT_REMOTE_PATH if args.variant == "remote" else PROMPT_LOCAL_PATH
    if args.prompt_file is not None:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    elif default_prompt_path.exists():
        prompt = default_prompt_path.read_text(encoding="utf-8")
    else:
        prompt = settings.router.classifier_prompt or DEFAULT_CLASSIFIER_PROMPT

    explicit = [m.strip() for m in args.models.split(",") if m.strip()] or None
    try:
        tags = list_ollama_tags(base_url, timeout_s)
    except httpx.HTTPError as exc:
        print(f"Warning: could not list tags ({exc}); using configured classifier only", file=sys.stderr)
        tags = []
    models = pick_classifier_models(tags, settings.router.classifier, explicit)
    if not models:
        models = [ollama_model_name(settings.router.classifier)]
    # Single-id runs: one classifier model only (configured / first explicit).
    if id_filter:
        models = models[:1]

    print(f"Ollama: {base_url}", flush=True)
    print(f"Variant: {args.variant} (gold map: {gold_map_path.name})", flush=True)
    print(f"Mode: {mode} ({len(dataset)} rows)", flush=True)
    print(f"Models: {', '.join(models)}", flush=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / ts
    all_details: list[DetailRow] = []
    summaries: list[ModelSummary] = []

    # Reuse one client for warm + all rows (live classify also avoids per-call TLS setup).
    with httpx.Client(timeout=max(timeout_s, 60.0)) as client:
        for model in models:
            try:
                warm_classifier(
                    client,
                    base_url=base_url,
                    model=model,
                    keep_alive=settings.ollama.keep_alive,
                )
            except httpx.HTTPError as exc:
                print(f"Warning: warmup failed for {model}: {exc}", file=sys.stderr, flush=True)
            details: list[DetailRow] = []
            fails = 0
            for i, row in enumerate(dataset, start=1):
                pred, latency_ms = classify_message(
                    client,
                    base_url=base_url,
                    model=model,
                    system_prompt=prompt,
                    message=row.message,
                    keep_alive=settings.ollama.keep_alive,
                )
                detail = score_row(row, pred, model, latency_ms)
                details.append(detail)
                if not detail.composite_ok:
                    fails += 1
                    print(
                        f"  FAIL [{row.id}] intent "
                        f"{detail.expected['intent']!r}->{detail.predicted['intent']!r} "
                        f"model {detail.expected['model']!r}->{detail.predicted['model']!r} "
                        f"tools_ok={detail.tools_ok} {detail.latency_ms:.0f}ms",
                        flush=True,
                    )
                    if detail.raw_response:
                        print(f"    raw: {detail.raw_response[:300]!r}", flush=True)
                elif i == 1 or i % PROGRESS_EVERY == 0 or i == len(dataset):
                    print(
                        f"  [{i}/{len(dataset)}] last={row.id} "
                        f"composite_so_far="
                        f"{sum(1 for d in details if d.composite_ok) / len(details):.1%} "
                        f"median_ms={_median([d.latency_ms for d in details]):.0f} "
                        f"fails={fails}",
                        flush=True,
                    )
            summaries.append(summarize(details, model))
            all_details.extend(details)

    write_run_artifacts(
        run_dir,
        prompt=prompt,
        mode=mode,
        variant=args.variant,
        gold_map_name=gold_map_path.name,
        settings=settings,
        summaries=summaries,
        details=all_details,
    )
    print_summary(summaries, run_dir)

    best = max((s.composite for s in summaries), default=0.0)
    if mode == "smoke" and best < args.min_accuracy:
        print(
            f"FAIL: best smoke composite {best:.1%} < --min-accuracy {args.min_accuracy:.0%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
