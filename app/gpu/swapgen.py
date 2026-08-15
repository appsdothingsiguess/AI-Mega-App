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
  --parallel 1  (global, in macro; see note below)

Live incident 2026-08-15 (traces 63825427/c7763bd7, chat eee45b51...): llama-
server's `-np`/--parallel default is -1 ("auto"), which for coder resolved to
4 slots (confirmed via GET /slots — id 0-3). This app is single-user and
sends no slot/session pinning, so llama-swap/llama-server round-robins each
request across slots; a multi-turn conversation almost never lands on the
same slot twice, so its KV-cache prefix is gone every turn even when the
prompt is a pure append. Observed cost: cache_n=0 and 10-14s of full prompt
reprocessing on every turn once a chat exceeds a couple of exchanges,
misread from the debug panel as a "model swap" (PLAN.md §4.2's model_loading
heuristic fires on any first-token gap > FIRST_TOKEN_WARN_S, swap or not).
Pinning --parallel 1 forces every model onto a single slot so consecutive
turns in the same chat always hit the same KV cache, and incidentally
reclaims the 3x extra context-memory auto mode had reserved per model.
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

    Deduplication key is (file, gpu), not file alone: models sharing the
    same blob AND the same device are the same underlying llama-server
    process (e.g. reasoner/chat-default, both gpu=0 — dedup to one swap
    entry). Models sharing a blob but placed on *different* devices (e.g.
    utility on cpu, utility-gpu on gpu=1) are genuinely separate processes
    and must both survive — keying on file alone would silently drop one.
    Priority: resident:true over non-resident; ties broken by first in list.
    """
    key_to_canonical: dict[tuple[str, object], str] = {}
    key_to_canonical_resident: dict[tuple[str, object], bool] = {}
    for m in config.models:
        if not m.enabled:
            continue
        key = (m.file, m.gpu)
        if key not in key_to_canonical:
            key_to_canonical[key] = m.name
            key_to_canonical_resident[key] = m.resident
        elif m.resident and not key_to_canonical_resident[key]:
            # A resident entry displaces a non-resident one for the same
            # (file, gpu) — but only once. If the canonical is already
            # resident, it keeps priority (first-in-list wins the tie), so a
            # second resident:true entry can't clobber it.
            key_to_canonical[key] = m.name
            key_to_canonical_resident[key] = True

    return [
        m
        for m in config.models
        if m.enabled and key_to_canonical.get((m.file, m.gpu)) == m.name
    ]


def generate(config: Config) -> str:
    """Render llama-swap YAML from config.  Pure function; byte-stable for
    a given config (no timestamps, sorted-by-definition, f-strings only).

    Callers: GET /api/gpu/swap-config (display), POST /api/gpu/apply (write).
    The function always returns YAML; the apply endpoint decides whether to
    write it based on config.gpu.enabled.
    """
    entries = _select_entries(config)

    # Group membership (PLAN §4.1 shape, extended 2026-08-15 for the
    # summarizer GPU1 fast path):
    # resident = resident:true AND gpu != 0  (CPU + GPU1 always-loaded)
    # gpu0-main = gpu == 0  (big-model swap slot, one active at a time)
    # gpu1-swap = gpu == 1 AND resident:false  (GPU1 on-demand swap slot,
    #   separate from the resident group so it never evicts dispatcher and
    #   dispatcher never blocks it — they coexist on the same card, VRAM
    #   permitting; see utility-gpu in config.yaml)
    resident_names = [e.name for e in entries if e.resident and e.gpu != 0]
    gpu0_names = [e.name for e in entries if e.gpu == 0]
    gpu1_swap_names = [e.name for e in entries if e.gpu == 1 and not e.resident]

    timeout = int(config.llama_swap.timeout_s)

    lines: list[str] = [
        "# generated — do not hand-edit",
        f"healthCheckTimeout: {timeout}",
        "macros:",
        f"  llama: {_LLAMA_BIN} --host 0.0.0.0 --port ${{PORT}} --jinja --parallel 1",
        "models:",
    ]

    for m in entries:
        cmd = _build_cmd(m)
        # env: always emitted — prevents CUDA context leaks on CPU entries
        # (CUDA_VISIBLE_DEVICES="" reclaims ~150-256 MB per card, defect #4).
        # CUDA_DEVICE_ORDER=PCI_BUS_ID accompanies every CUDA_VISIBLE_DEVICES
        # pair — without it the wrong GPU gets pinned (llm-stack/CLAUDE.md:76-78).
        if m.gpu == "cpu":
            env_visible = '"CUDA_VISIBLE_DEVICES="'
        else:
            env_visible = f'"CUDA_VISIBLE_DEVICES={m.gpu}"'
        env_order = '"CUDA_DEVICE_ORDER=PCI_BUS_ID"'

        lines.append(f"  {m.name}:")
        lines.append(f"    cmd: {cmd}")
        lines.append(f"    env: [{env_visible}, {env_order}]")

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
    if gpu1_swap_names:
        gpu1_members = ", ".join(gpu1_swap_names)
        lines.append(f"  gpu1-swap: {{ swap: true, members: [{gpu1_members}] }}")

    return "\n".join(lines) + "\n"
