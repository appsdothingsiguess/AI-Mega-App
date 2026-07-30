"""Deterministic llama-swap YAML generator (PLAN.md §4.1, docs/FEATURES.md F14).

Turns a validated Config into a byte-stable llama-swap config YAML.  Output
is hand-written f-strings — no templating engine, no AI generation, no YAML
library round-trip that could alter ordering or quoting.

Phase-0 acceptance criteria (four observed defects now fixed here):
  1. groups: block always emitted — CPU residents in resident{swap:false},
     GPU0 models in gpu0-main{swap:true}.  Without it every model falls into
     llama-swap's implicit default swap group and CPU residents get evicted.
  2. Placement via CUDA_VISIBLE_DEVICES in env:, never --tensor-split.
     Measured ~3x slower (docs/phase0-measurements.md §8).
  3. --reasoning off on every reasoning_off:true alias.  Without it the
     classifier spends its budget in <think> and returns empty content
     (91.76% accuracy does not reproduce). Flag spelling: --reasoning off,
     from PLAN.md §4.1 sample and phase0-measurements.md §13.
  4. CUDA_VISIBLE_DEVICES="" on CPU-placed entries.  --device none -ngl 0
     alone still initialises CUDA contexts worth ~150-256 MB per card.

Flag spellings verified in PLAN.md §4.1 and docs/phase0-measurements.md:
  --embedding  (singular — PLAN §4.1 sample; not --embeddings)
  --reasoning off  (server-level; not /no_think prompt suffix)
  --device none -ngl 0  (CPU placement)
  -ngl 999  (GPU placement)
  --mmproj  (vision models)
  --jinja  (global, in macro)
"""

from __future__ import annotations

from app.config import Config, ModelEntry

# llama-server binary path on ailab (PLAN.md §4.1, locked roster).
_LLAMA_BIN = (
    "/home/john/llm-stack/engine/llama.cpp/build/bin/llama-server"
)

# Classes that get --temp 0 (single-token / structured output callers).
_TEMP_ZERO_CLASSES = frozenset({"classifier", "dispatcher"})


def _build_cmd(m: ModelEntry) -> str:
    """Build the llama-server command string for one model entry.

    Flag order follows PLAN.md §4.1 sample exactly to keep golden tests
    meaningful:
      ${llama} -m FILE  [--device none -ngl 0 | -ngl 999]
               [--embedding]  -c CTX  [--mmproj PATH]
               [--reasoning off]  [--temp 0]  [extra_flags...]
    """
    parts: list[str] = ["${llama}", "-m", m.file]

    if m.gpu == "cpu":
        parts += ["--device", "none", "-ngl", "0"]
    else:
        parts += ["-ngl", "999"]

    if m.class_ == "embed":
        parts.append("--embedding")

    parts += ["-c", str(m.ctx)]

    if m.mmproj:
        parts += ["--mmproj", m.mmproj]

    if m.reasoning_off:
        parts += ["--reasoning", "off"]

    if m.class_ in _TEMP_ZERO_CLASSES:
        parts += ["--temp", "0"]

    parts.extend(m.extra_flags)

    return " ".join(parts)


def _select_entries(config: Config) -> list[ModelEntry]:
    """Return enabled, deduplicated entries in config list order.

    Deduplication: models sharing the same file path keep exactly one entry.
    Priority: resident:true over non-resident; ties broken by first in list.
    This collapses reasoner (same blob as chat-default, thinking enabled at
    the request layer) into chat-default — llama-swap sees one swap slot.
    """
    file_to_canonical: dict[str, str] = {}
    for m in config.models:
        if not m.enabled:
            continue
        if m.file not in file_to_canonical:
            file_to_canonical[m.file] = m.name
        elif m.resident:
            # A resident entry displaces a non-resident one for the same file.
            file_to_canonical[m.file] = m.name

    return [
        m
        for m in config.models
        if m.enabled and file_to_canonical.get(m.file) == m.name
    ]


def generate(config: Config) -> str:
    """Render llama-swap YAML from config.  Pure function; byte-stable for
    a given config (no timestamps, sorted-by-definition, f-strings only).

    Callers: GET /api/gpu/swap-config (display), POST /api/gpu/apply (write).
    The function always returns YAML; the apply endpoint decides whether to
    write it based on config.gpu.enabled.
    """
    entries = _select_entries(config)

    # Group membership (PLAN §4.1 shape):
    # resident = resident:true AND gpu != 0  (CPU + GPU1 always-loaded)
    # gpu0-main = gpu == 0  (big-model swap slot, one active at a time)
    resident_names = [e.name for e in entries if e.resident and e.gpu != 0]
    gpu0_names = [e.name for e in entries if e.gpu == 0]

    timeout = int(config.llama_swap.timeout_s)

    lines: list[str] = [
        "# generated — do not hand-edit",
        f"healthCheckTimeout: {timeout}",
        "macros:",
        f"  llama: {_LLAMA_BIN} --host 0.0.0.0 --port ${{PORT}} --jinja",
        "models:",
    ]

    for m in entries:
        cmd = _build_cmd(m)
        # env: always emitted — prevents CUDA context leaks on CPU entries
        # (CUDA_VISIBLE_DEVICES="" reclaims ~150-256 MB per card, defect #4).
        if m.gpu == "cpu":
            env = '"CUDA_VISIBLE_DEVICES="'
        else:
            env = f'"CUDA_VISIBLE_DEVICES={m.gpu}"'

        lines.append(f"  {m.name}:")
        lines.append(f"    cmd: {cmd}")
        lines.append(f"    env: [{env}]")

        # ttl: emit when resident or ttl_s == 0; else emit numeric; else omit.
        if m.resident or m.ttl_s == 0:
            lines.append("    ttl: 0")
        elif m.ttl_s is not None:
            lines.append(f"    ttl: {m.ttl_s}")

    # groups (inline flow style matching PLAN §4.1 sample)
    res_members = ", ".join(resident_names)
    gpu0_members = ", ".join(gpu0_names)
    lines.append("groups:")
    lines.append(
        f"  resident: {{ swap: false, exclusive: false, members: [{res_members}] }}"
    )
    lines.append(f"  gpu0-main: {{ swap: true, members: [{gpu0_members}] }}")

    return "\n".join(lines) + "\n"
