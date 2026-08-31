# Handoff notes (agent-only)

Working notes for whichever Claude Code session picks this up next.
Not a planning doc, not user-facing — just context that isn't obvious
from the code alone. The dated entries below the current-state notes are
history, not live deployment instructions; use `AGENTS.md` and
`docs/AGENT_CONTEXT_MEGA.md` for the current service/roster truth. Delete or
trim entries once they're stale.

## 2026-08-30 — Qwen3.6 isolated 32K service and diagnostic relay

For the isolated Qwen3.6 worker test, `llama-swap.service` is intentionally
stopped so its GPU1 residents (`dispatcher` and `utility-gpu`) do not consume
the 3070's VRAM. Qwen3.6 is now managed by the user service
`qwen36-ngram.service`, listening on `0.0.0.0:5807` with alias
`qwen3.6-35b-ngram` and the tested profile: 32,768 context, 12 GPU layers,
q8 KV, Flash Attention, 12 CPU threads, batch/ubatch 2048/256, one slot,
reasoning off, and `ngram-mod` (match 24, max 12). Startup runs
`scripts/warmup_openai_server.py`; the warmup completed successfully at
about 13.2 tok/s. The model reports `n_ctx: 32768` and uses about 6.6 GiB on
GPU1.

The matching capture relay is managed by `pi-qwen36-relay.service` on
`0.0.0.0:8082`, forwarding to `127.0.0.1:5807` and writing captures to
`/tmp/pi-qwen36-captures/`. It allows only the Windows Harness client
`192.168.0.246`, just as the existing 8081 relay allows that client for the
llama-swap/Qwen3.8 route. Use `http://192.168.0.89:8082/v1` in DeepSeek
Harness with model `qwen3.6-35b-ngram`, `contextWindow: 32768`, and text-only
input. Localhost requests correctly receive 403; validate successful relay
forwarding from the Windows client. The normal app backend is offline while
this isolated test is active.

## 2026-08-30 — Qwen3.8 text-only vs vision and Qwen3.6 GPU1 offload tests

Production services were stopped for isolated testing; no config or generated
llama-swap file was changed. Using the checked-in benchmark harness and the
same Qwen3.8 MTP/KV/batch settings at 90K context, omitting the BF16 vision
projector used 22,118 MiB on GPU0 and produced 73.98/75.07 end-to-end decode
tok/s. Loading `mmproj-BF16.gguf` used 23,256 MiB and produced
73.77/74.11 tok/s. The projector therefore costs about 1.1 GiB without a
measurable shallow text decode benefit; keep it on the vision-capable alias,
not ordinary text aliases. Owner reports that real longer-context vision
requests fall to about 60 tok/s while text remains about 75 tok/s; this is
consistent with the projector/prefill and image-token workload and should be
measured with the image prompt suite before changing context defaults.

Qwen3.6-35B-A3B-UD-Q4_K_M on GPU1 with 12 GPU layers (remaining layers in
system RAM), q8 KV, Flash Attention, one slot, and 12 CPU threads remained
stable at 16K, 32K, and 64K: 12.1–13.3 tok/s and 6.6–6.8 GiB VRAM. Increasing
batch/ubatch to 4096/512 and CPU threads to 16 did not improve throughput.
This is below the 20–30 tok/s worker target.

Built-in llama.cpp n-gram speculation (`spec-type=ngram-mod`, match 24,
draft max 12) is a large coding-pattern win: at both 16K and 64K, the first
request was ~12.6 tok/s, then repeated Fibonacci/code requests reached
50.4–53.2 tok/s with ~6.5–6.8 GiB GPU1 usage. This is a warm, highly
predictable-code result; prose and novel tool output require a separate
quality/acceptance check. It is currently the best stock-runtime worker
profile, but should remain an isolated alias until agent transcripts confirm
that n-gram guesses do not reduce correctness.

The current `bench_server.py` intentionally starts with `-ngl 999`; this
prevents llama.cpp's automatic `--fit` from running (the runtime reports
"n_gpu_layers already set by user"). A subsequent `--n-cpu-moe 32` run
overlapped the owner's restart of llama-swap, so its GPU0 reading is
contaminated by the production `chat-default` process and is invalid for
performance comparison. Do not adopt it. The harness needs an explicit
placement/fit mode and a service-free rerun before a fair `--fit-target` or
MoE-placement sweep can be added.

Community tuning points to test next, not blindly adopt: `--fit on` with an
explicit `--fit-target`/margin, MoE-aware `--n-cpu-moe` placement rather than
arbitrary layer counts, and native Qwen3.6 MTP builds/models with
`--spec-draft-n-max 2` or 3. A representative Reddit Qwen3.6 MTP setup uses
`-fitt 1536`, q8 main/draft KV, `--no-mmap --mlock`, and reports 70–82 tok/s on
a much faster RTX 4070 Super with a custom MTP PR/model; it is not directly
comparable to this stock Q4 model/runtime. References:
https://www.reddit.com/r/LocalLLaMA/comments/1t82zxv/80_toksec_and_128k_context_on_12gb_vram_with/
https://www.reddit.com/r/LocalLLaMA/comments/1snt811/anyone_who_tried_new_36_on_single_3090_whats_your/
https://www.reddit.com/r/LocalLLM/comments/1vq5oyu/guide_for_running_dense_models_on_16_gb_vram_qwen/

### Superseded Qwen3.6 Harness setup

The former foreground 65K server, direct `:5807` route, and duplicated Windows
provider examples are retired. The managed 32K worker and the two relay
endpoints are documented once in the 2026-08-30 entry above and in
`AGENTS.md`; do not revive the old configuration from this file.


## 2026-08-28 — DeepSeek Harness custom-provider vision setup

The Pi/DeepSeek Harness capture relay listens on `192.168.0.89:8081` and
forwards chat-completions to llama-swap at `127.0.0.1:8080`. It now forwards
`GET /v1/models` as well, because Harness uses that endpoint during custom
provider model discovery. The relay remains restricted to the configured Pi
client (`192.168.0.246`) and remains POST-only for captured inference traffic.

The Harness user config is `/home/john/.dsh/settings.yaml`. Models entered on a
custom provider are text-only unless their model entry declares modalities;
Qwen3.8 aliases served by AI Mega App therefore use:

```yaml
input: [text, image]
```

This metadata is a Harness-side claim and is separate from llama.cpp's
`--mmproj` runtime flag. The latter is configured in `config.yaml` and the
generated llama-swap config for `chat-default`, `coder`, `reasoner`, and
`vision`.


## 2026-08-27 — live coding comparison prompt: summary coverage trust diagnostics

Use this identical prompt for a real implementation comparison between
`chat-default` and `coder-alt`. Run both in disposable worktrees from the same
clean base commit, with the same token budget (at least 4096). Preserve each
diff and test output; assess correctness, scope discipline, and code clarity.

```text
You are working in the AI-Mega-App repository. Implement this feature end to
end; do not change config, database schema, SSE event vocabulary, generated
web/js files, dependencies, or unrelated code.

Feature: Summary coverage trust diagnostics in the Debug panel.

The app stores rolling-summary coverage metadata in the newest `summary` span.
Today `app/background/summary_coverage.py:trusted_covered_count()` returns an
integer or None, so the Debug panel cannot explain why it must fall back to
raw history. Add a structured, conservative verdict and display it.

Requirements:
1. In `app/background/summary_coverage.py`, add a typed structured result for
   the trust decision and a public `coverage_verdict(...)` helper. It must
   report `trusted`, `covered_count`, and one of these stable reason strings:
   `ok`, `no_summary`, `failed_summary`, `missing_metadata`,
   `count_out_of_range`, `prefix_mismatch`, or `summary_mismatch`.
2. Preserve the public behavior of `trusted_covered_count(...)` by delegating
   to the verdict: return the count only when trusted, otherwise None. Do not
   weaken existing fingerprint or newest-summary safety checks.
3. Classify precisely: no committed chat summary is `no_summary`; a newest
   span with `error` is `failed_summary`; no summary span, malformed span
   data, or absent/wrong-type coverage fields is `missing_metadata`; an
   invalid count is `count_out_of_range`; fingerprint failures use their
   respective mismatch reasons. A failed or malformed newest attempt remains
   authoritative over an older good one.
4. Extend `GET /api/debug/summary-status?chat_id=` through its existing
   status path. Keep current fields and add `coverage` with exactly `trusted`
   (boolean), `covered_message_count` (integer or null), and `reason` (the
   stable string). Compute it from the verdict, not a duplicate check.
5. Update the existing Debug summary-status panel and its TypeScript type to
   show either `summary coverage: trusted (N messages)` or `summary coverage
   unavailable: <human-readable reason>`. Escape dynamic text and retain the
   graphite/indigo compact style.
6. Add focused tests: every verdict reason, old count-helper behavior, newest
   failed attempt overriding an older valid one, and the summary-status API
   payload. Add a frontend rendering/type test if supported. Run relevant
   pytest tests and `npx tsc --noEmit`.

Read AGENTS.md, PLAN.md, docs/FEATURES.md, and relevant existing
summary-status/debug code first. In the final response list changed files,
commands/results, and limitations. Do not leave an unused helper: the Debug
panel must consume the new API field.
```

### Expected implementation and grading guide

The intended boundary is `summary_coverage.coverage_verdict` ->
`summary_status.summary_status` -> `/api/debug/summary-status` ->
`web/src/types.ts` -> `web/src/views/debug.ts`. Make
`trusted_covered_count` a compatibility wrapper around the verdict; do not
duplicate trust checks. No migration or span stage is required: use the
newest existing `summary` span and fingerprints.

The existing top-level `covered_message_count` remains zero when untrusted.
Add the nested, nullable truth:

```json
{
  "covered_message_count": 0,
  "coverage": {
    "trusted": false,
    "covered_message_count": null,
    "reason": "missing_metadata"
  }
}
```

A committed summary with no matching span is `missing_metadata`, not
`failed_summary`; this is a key grading case. Tests should use real
chat/message/span fixtures and cover matching metadata, no chat summary,
explicit latest-span error, no span/malformed JSON/missing fields,
boolean/out-of-range count, both fingerprint mismatches, and a newer failed
span overriding an older valid one. The implementation is incomplete if only
the helper or tests exist—the Debug panel must display the API-backed verdict.

## 2026-08-26 — Pi capture relay installed persistently

The Pi capture relay is now a real enabled `systemd --user` service,
`pi-capture-relay.service`, backed by
`scripts/pi_request_capture_proxy.py` and enabled with `loginctl` lingering for
user `john`. It listens on `0.0.0.0:8081`, forwards to `127.0.0.1:8080`, allows
client `192.168.0.246`, and retains prompt-bearing captures in
`/tmp/pi-request-captures/`. The installed unit source is
`ops/pi-capture-relay.service`; the service log is available through its user
service status and captures remain in `/tmp`.

Pi endpoint: `http://ailab:8081/v1`. Check it with
`systemctl --user status pi-capture-relay.service` and
`ss -ltnp | rg ':8081\\b'`. If the laptop IP changes, update
`PI_PROXY_ALLOWED_CLIENT` in `ops/pi-capture-relay.service`, reinstall the
unit, then run `systemctl --user daemon-reload && systemctl --user restart
pi-capture-relay.service`.

## 2026-08-25 — config apply now reloads disk state and live-verified

Fixed the recurring stale-config path. `settings.local.yaml` was a legacy full
roster that overrode newer `config.yaml` model flags; it is now sparse and
contains only intentional UI overrides. Model defaults/performance flags are
owned by `config.yaml`. `POST /api/gpu/apply` now reloads the production config
from disk before calling swapgen, while injected test configs remain static.

Live proof: temporarily changed `chat-default --cache-reuse 256` to `257`,
restarted only `ai-mega-app`, POSTed `/api/gpu/apply`, and
`scripts/load_model_check.py chat-default` showed the live server at `257`.
Restored `256`, POSTed apply again, and verified the live command returned to
`256` with `--reasoning off`, MTP sidecar, n-max 4, q8 KV, batch 2048, and
ubatch 256. `config_drift_check.py` reported no drift on both passes.

Use `python3 scripts/load_model_check.py <alias>` to trigger lazy loading and
print the parsed live llama-server command. The broad pytest run still stalls
at the pre-existing suite point after roughly 34 tests; focused config/swapgen
tests (48) and the reload regression pass, as does `npx tsc --noEmit`.

## 2026-08-25 — coder-alt live quality testing started

The live service currently has `coder-alt` loaded on GPU0 (Ornith-1.5-35B,
server port 5803); all other roster models are unloaded. GPU0 was using about
22.9 GiB of 24 GiB during the check. This is an active quality-test session,
not a config-apply session.

Current evaluation prompt:

> Read AGENTS.md and inspect the repository. Implement one small, test-backed feature: Do not modify unrelated files. Run the focused tests and report files changed.

The purpose is to assess repository instruction-following, scope discipline,
small-feature implementation, focused verification, and reporting quality.
Preserve the repository's existing user changes. In particular, do not reset
the current checkout: it is `main` with uncommitted config/docs/benchmark
changes, not a dedicated test branch. Do not apply or restart llama-swap while
running this quality test.

Previous coder-alt optimization result remains the recommended runtime:
130K context, Flash Attention, temperature 0.6, top-p 0.95, top-k 20,
12 threads, batch 2048, ubatch 128, with `--parallel 1` supplied by the
llama-swap macro. The config and settings overlay are prepared but the live
deployment should only be changed explicitly by the owner.

## 2026-08-24 — coder-alt optimization results; config prepared, not live-applied

Ran isolated GPU0 tests against `Ornith-1.5-35B-Q4_K_M.gguf` using the
repository benchmark harness, with GPU1's resident production models left
untouched. The model is stable at the configured 130,000-token context and
fits solo on the RTX 3090 with roughly 1.7 GiB headroom.

The tested matrix was:

| Context | Batch | Ubatch | Average generation | Peak GPU0 VRAM |
|---:|---:|---:|---:|---:|
| 130K | 2048 | 128 | 151.58 tok/s (3-slot harness) | 23,098 MiB |
| 130K | 2048 | 256 | 148.74 tok/s (3-slot harness) | 23,142 MiB |
| 130K | 2048 | 512 | 150.06 tok/s (3-slot harness) | 23,230 MiB |
| 130K | 4096 | 512 | 145.66 tok/s (3-slot harness) | 23,230 MiB |
| 160K | 2048 | 256 | 146.47 tok/s (3-slot harness) | 23,744 MiB |

The final production-shaped single-slot run (`--parallel 1`) with 130K,
2048/128 averaged 148.74 tok/s across five requests and peaked at 22,898
MiB. Therefore `--batch-size 2048` plus `--ubatch-size 128` is now prepared
in `config.yaml`; `--parallel 1` remains supplied by the llama-swap macro.
No live apply, service restart, or generated deployment-file change was
performed.

Growing-context recall stayed correct at every checkpoint through 105,479
actual prompt tokens, but generation fell from 22.3 tok/s at 2,010 prompt
tokens to 1.22 tok/s at 84,437 tokens. Keep 130K as the capacity ceiling,
not as a claim of fast interactive generation at the far end.

The safe six-case coder-debug transcript run completed without request
errors with a 5,120-token allowance. Manual review found five fully correct
diagnoses and one partial result: the NumPy diagnosis and fix were right but
the removal version was misstated. The compile/run evaluator was not run
because it executes model-generated code with host privileges.

## 2026-08-24 — Qwen3.8 separate-MTP tuning results

The official `mtp-Qwen3.8-27B-Q4_0.gguf` sidecar was downloaded to
`/home/john/llm-stack/models/gguf/` and verified against the published SHA256
`051a1764cff8c4f3ee6ae8b00593a0364c7539c67fa50ffc58f3f96509fca38e`.
The exact sidecar-loaded command was tested isolated on GPU0 with
`GGML_CUDA_GRAPH_OPT=1`, the Qwen3.8 Q4_K_XL main model, `-ngl -1`, `-ngld -1`,
`-c 90000`, `--spec-type draft-mtp`, q8 draft/main KV, `-b 2048`, `-ub 512`,
`--cache-reuse 256`, `--parallel 1`, and top-p/min-p/presence/frequency values
all at the owner's deterministic settings. Startup logs explicitly showed the
sidecar loading and GPU0 used ~22,136 MiB.

Short isolated comparison (one shallow 66-token prompt; not a general ranking):

| Settings | llama.cpp eval tok/s | Draft acceptance |
|---|---:|---:|
| `--spec-draft-p-min 0.75`, `--temp 0.0` | 70.51 | 89.34% (109/122) |
| `--spec-draft-p-min 0.75`, `--temp 0.64` | 68.32 | 88.89% (104/117) |
| Owner's full command, no p-min/temp override | **75.40** | 73.21% (123/168) |

The full command was fastest in this short sample despite lower draft
acceptance; more repeated and deep-context coding prompts are needed before
choosing `p-min` as a production default. The sidecar was confirmed active in
all three runs. Production config was not changed by these isolated tests.

Four-request repeat using the full command (same shallow prompt, one server
boot, four sequential requests) produced llama.cpp eval speeds of 76.45,
73.94, 75.53, and 67.08 tok/s: **73.25 tok/s average**. Draft acceptance was
75.93%, 69.94%, 72.12%, and 60.32%: **69.57% average**. End-to-end harness
generation rates were 66.97, 64.29, 66.44, and 59.89 tok/s. This confirms the
~70 tok/s range is reachable, while acceptance variance explains the lower
tail. The four-run log is `logs/benchmarks/server/qwen38-mtp-full-settings-4x.jsonl`;
the detailed llama.cpp log was `/tmp/qwen38-mtp-full-settings-4x.log`.

## 2026-08-24 — applied: utility-gpu q8_0 KV fix + universal "resume-exactly" summary prompt (G2)

Both fixes documented/validated in the entry below (unchanged, kept for
history) are now **applied**, and superseded once more same-day (see the
prompt-collapse finding in `docs/current_bugs.md`'s newest entry at the
time of writing):

- `config.yaml` `utility-gpu.extra_flags`: `q4_1`/`q4_1` → `q8_0`/`q8_0`
  KV cache (comment updated in place with the full before/after numbers).
  Unchanged since first applied.
- `app/background/summary_policy.py` `_SUMMARY_PROMPT`: first swapped to
  the coding-shaped "Variant B" prompt (Files and locations
  touched/Changes made and why/Tests added-needed/Config changed/Next
  steps), then **replaced again same day** after a follow-up test found
  Variant B **collapses to a near-empty summary (32 completion tokens,
  bare section headers, zero content) on non-coding chats** — its rigid
  template has nothing to fill when the content doesn't match (e.g. the
  Vael Archipelago prose case). That's a worse failure than the original
  fact-loss bug: total information loss, silently written to
  `chats.summary`. The replacement, "G2" in `docs/current_bugs.md`'s
  task-adaptive-prompt entry, reframes the objective as "produce working
  context so the same agent/user can resume exactly where things left
  off" (the framing that makes Claude Code's own `/compact` avoid heavy
  loss) with content-agnostic sections (Entities and specifics stated /
  Decisions made and why / Commitments or constraints stated / Open
  questions-next steps) plus an explicit verbatim-preservation
  instruction. G2 avoided the collapse on both prose and coding content in
  testing and scored competitively with (sometimes better than) the
  content-specific prompt even on coding content — recommendation was a
  **single universal prompt over content-adaptive selection**, since
  adaptive selection reintroduces the same collapse risk on any
  misclassified or mixed-content chat. This is what's live now.

**Verification gate**: `npx tsc --noEmit` clean, `pytest`: 208/208
passing. Along the way, fixed a **pre-existing, unrelated** failure
(`tests/test_swapgen.py::test_golden`) — its hardcoded `GOLDEN` fixture
was stale against three already-approved config.yaml changes made in
*prior* sessions before this one touched anything (confirmed via `git
diff --stat`): chat-default/coder `ctx` 262144→131072 and coder-small
`ctx` 8192→30000 (both from the 2026-08-23 capacity work), plus this
session's own utility-gpu KV-cache change. Synced the hand-maintained
`GOLDEN` string to match `generate()`'s real output — mechanical, not a
config decision.

**Not yet done**: live apply through llama-swap (`app/gpu/swapgen.py`
regenerate + `/api/gpu/apply`) and a real end-to-end chat/summary cycle
against the running app — every fix in this entry is validated in
isolation (direct llama-server, not through llama-swap) and via the
verification gate, but not yet confirmed under the actual service.
`llama-swap.service` remains inactive; starting it needs explicit
approval (sudo territory per `.cursor/rules/008-remote-box.mdc`).

## 2026-08-23 — Qwen3.8/vision context re-test, utility-gpu speed fix (found, not applied), summary-quality live repro

Session picked up the paused 2026-08-23 isolated-capacity work per owner
request: box confirmed idle (both GPUs ~0 MiB, `llama-swap.service`
inactive) before every test below. All isolated `llama-server` runs used
`CUDA_VISIBLE_DEVICES`/`CUDA_DEVICE_ORDER=PCI_BUS_ID` solo-pinning, never
`--tensor-split`, per PLAN.md §4.1. `llama-swap.service` was never started;
no config file was touched except the one summarized at the end of this
entry (not yet applied — awaiting explicit apply).

### Qwen3.8 (chat-default/coder blob) context ceiling — re-tested, did not reproduce prior OOM

262144 is the model's actual `n_ctx_train` (llama.cpp clamps anything
higher, confirmed via a 270336 attempt logging
`n_ctx_seq (270336) > n_ctx_train (262144)`) — there is no higher ceiling
to bisect toward. The 2026-08-23-earlier-session OOM at 262144 (MTP's
second KV buffer) **did not reproduce** on a clean idle GPU0 today: every
tested point from 196608 up through 261888 and 262144 itself booted
cleanly, served a real completion, and ran its MTP draft path successfully
(draft_n=200, accepted=97). VRAM at 262144 was 23,450/24,576 MiB — only
~1.1 GiB headroom, thin enough that the earlier OOM was plausibly
transient fragmentation/prior-process state rather than a hard wall.
**Recommendation: keep `ctx: 131072` in config.yaml** (unchanged) rather
than trusting a 1.1 GiB-margin config off one clean-box run — would need
re-verification under real llama-swap multi-model residency before
raising it.

Depth matters more than the ctx ceiling for throughput: 65 tok/s decode on
a shallow 17-token prompt vs **17.3 tok/s at a 6,621-token-deep prompt**,
same 262144-ctx server (MTP draft acceptance also dropped, 97/200 ≈ 48.5%
at depth). This is the real number to judge the reasoning-role tok/s bar
against, not the shallow-prompt number.

### Vision (Qwen3-VL-32B) context — tested for the first time, throughput-bound not VRAM-bound

True native ceiling confirmed 262144 (GGUF metadata: `qwen3vl.context_length`).
Every size 8192→262144 boots and stays coherent (verified against the real
6 image-grounded prompts in `scripts/eval_data/vision_prompts.json` +
`scripts/eval_data/images/` — exact text-read and correct chart reasoning
held even at 262144). VRAM is never the constraint (weights+mmproj ~20GB
dominate; headroom stayed ~1.5-2GB at every size tested) — decode speed is:
8192 (current shipped) 37.9 tok/s → 16384: 12.8 → 32768: 6.0 → 262144: 1.97
tok/s (~19x slower than shipped). `--flash-attn` made **zero** measurable
difference at any size (same VRAM, same speed, byte-identical output) —
an external report's "fixes garbled Q4_K_M output" claim did not
reproduce here. **8192 is already near the throughput/context sweet spot**
for this model; a bump to 16384-24576 is technically safe (~3x slower) if
longer documents are needed, but 262144 costs ~19x and isn't practical
interactively. No config change made — this is an owner tradeoff call.

### Model-roster test coverage snapshot (as of today)

| Model | Status |
|---|---|
| chat-default/coder (Qwen3.8) | Context ceiling re-verified today (see above). Structured JSON 10/10 (prior session). Coding/reasoning quality suites still incomplete (interrupted prior session) — reasoner-vs-DeepSeek-R1-32B comparison not yet run. |
| coder-small | Fully validated (prior session): 32,768 native, 30,000 shipped, 112.3 tok/s. Still needs live apply/regenerate validation through llama-swap (only isolated so far). |
| vision (Qwen3-VL-32B) | Tested today for the first time (see above). |
| reasoner-alt (DeepSeek-R1-32B) | **Still untested** — no capacity/throughput/quality data exists for this model in any session. |
| dispatcher | Only an old llama-bench prompt/decode screen; co-residency measured today only incidentally (see utility-gpu section) — 1,303 MiB solo. |
| utility-gpu | Root-caused and fixed today (see below) — was the most urgent finding. |
| utility (CPU fallback), classifier, embed | Still untested this sweep. |

### utility-gpu (summarizer) — root cause found: q4_1 KV cache, not flash-attn

User-reported: summarization "super slow even on GPU" at 4-8k tokens.
Confirmed real in production data (`data/app.db` `summary` spans,
corroborated independently by `journalctl -u llama-swap`): 8k-token
summaries taking 53-159s wall-clock, effective ~50-150 tok/s — some
timing out outright at the 180s `summary_timeout_s` ceiling with **both**
`utility-gpu` and the CPU `utility` fallback failing back-to-back (e.g.
chat `f524f4b2c1a94fd598ca373f78e6ee9d`, traces `c0e491e8-…`/`5211bb21-…`,
both 180.001s timeouts before a third attempt, `b6c56923-…`, finally
succeeded at 53.2s/7964 prompt tokens/358 completion tokens — see the
live-repro section below for the quality read on that successful one).

First hypothesis (missing `--flash-attn` alongside `--cache-type-k/v
q4_1`, added 2026-08-21 to fix an unrelated KV-buffer OOM) was **wrong**:
isolated GPU1 (RTX 3070) A/B testing showed flash-attn made no measurable
difference at q4_1 (45.7-46.8 tok/s prefill either way). **The actual
cause is the q4_1 KV quant itself** — on this GPU/build it falls onto an
unaccelerated path for this model regardless of flash-attn:

| KV type | Prefill tok/s | Decode tok/s | Wall (9247+~70 tok) | Solo VRAM |
|---|---|---|---|---|
| q4_1/q4_1 (current prod) | 46.8 | 6.46 | 211.2s | 5645-5667 MiB |
| q4_1/q4_1 + flash-attn | 45.7 | 6.48 | 215.6s | 5645-5667 MiB |
| f16/f16 | 2710.8 | 59.65 | 4.59s | 7251-7273 MiB |
| **q8_0/q8_0 (recommended)** | 2602.8 | 53.90 | 4.82s | 6173-7495 MiB |

f16 is fastest solo but doesn't survive co-residency: 7,251 MiB +
dispatcher's 1,303 MiB = 8,554 MiB, over the 3070's 8,192 MiB budget.
q8_0 does fit — measured 7,495 MiB total with both models loaded and
utility-gpu mid-generation (~700 MiB headroom). **Net: ~55x prefill / ~8x
decode speedup over current production** (2603 vs 46.8 tok/s prefill;
53.9 vs 6.46 tok/s decode).

**Recommended change, not yet applied** (awaiting owner apply — config.yaml
key change): `utility-gpu.extra_flags` — replace
`["--cache-type-k","q4_1","--cache-type-v","q4_1"]` with
`["--cache-type-k","q8_0","--cache-type-v","q8_0"]`. No flash-attn flag
needed (neutral either way).

Also observed but not yet independently confirmed as primary: warmup
pings `utility-gpu` unconditionally every 300s
(`app/main.py`/`app/warmup.py`) and its own ping latency climbed from
~25ms to 700ms+ over one session — real GPU1 contention, likely secondary
to the KV-quant cause above, not yet isolated.

### Live summary-quality repro: trace `29266240-06e8-4b7b-85d6-f802d7317333`

Investigated per owner request. This trace itself is only a `title` span;
the actual chat is `f524f4b2c1a94fd598ca373f78e6ee9d` — a single 33,890-char
user message (a pasted fictional Victorian naturalist's journal, "The Vael
Archipelago") plus a short assistant reply, immediately followed by 3
summary attempts (2 timeouts, see above, then a 53.2s success). Only 2
messages total — this is the same "giant first message triggers immediate
oversized-summarization" shape already on record in `docs/current_bugs.md`
("P0 — giant messages break summarization"), now with a second concrete
example.

**Quality read on the successful summary** (`covered_message_count: 2`,
structured key-facts/decisions/preferences/open-questions format): topically
faithful — correctly identifies the fictional Victorian field-guide
premise, the 1847-1853 timeframe, and the ecological/cultural scope — but
drops every concrete number from the source (eleven major islands, four
hundred leagues from the Thornwall Coast, nine hundred fathoms depth, the
1791 coral-harvest decline date). Same lossy pattern already documented for
trace `cee82d55-…`'s 45-fact list: broad synopsis preserved, exact
indexed/numeric details did not survive. This is now confirmed across two
independent real chats, not a one-off.

### Muse Glimmer 30B — researched, not downloaded

Real model (Meta Superintelligence Lab, released 2026-08-10, Apache 2.0,
agentic/multimodal/reasoning, distilled from Muse Spark). GGUF available
at `huggingface.co/unsloth/Muse-Glimmer-30B-GGUF` (no plain Q4_K_M — closest
is `UD-Q4_K_XL`, ~15.9GB, matching this project's existing Q4_K_XL
convention for chat-default/coder) with a bundled `dflash-kquant.gguf`
(~1.63GB) DFlash draft model for speculative decoding. **Risk flagged, not
yet resolved**: `muse-glimmer` architecture support only merged into
upstream llama.cpp on 2026-08-10 (release b10353) — this box's prebuilt
binary predates that unless separately confirmed otherwise; a subagent
attempt to download+configure it was blocked by the auto-mode permission
classifier (bundled a large download + config.yaml edit into one
autonomous action). Download/smoke-test commands were handed to the owner
directly instead of run. Not added to config.yaml in any form.

## 2026-08-23 — summary-fidelity investigation (current handoff)

### User-reported issue

The user suspects that conversation summarization is losing useful model
context, especially when a smaller model is involved. They asked for a
manual investigation of recent chat logs, including trace
`cee82d55-be7d-446a-a316-d42810853a4a`.

### Finding

The suspicion is substantially correct. This is primarily a **summary
semantic-loss/policy failure**, not simply a small-model capability issue.
The test chat contained a numbered list of 45 facts. The original exact
mappings included FACT 7 = Solar System, FACT 12 = Earth’s Core, and FACT 31
= Carbon Footprint. The successful summary trace
`0b3368a3-698d-4fe7-91ee-97a28fa5db32` marked 9 messages covered, but reduced
the list to a broad topic-level synopsis. Later prompts intentionally omitted
the original messages and trusted that summary. The `coder-small` trace then
answered FACT 7 with the wrong item; a later `chat-default` response admitted
that FACTs 8–11 were unavailable.

Summary generation took about 93 seconds and returned no error. That latency
is worth separate performance follow-up, but it was not the data-loss cause.
The structural coverage metadata was internally consistent; the summary
content was not lossless enough for indexed, user-editable data.

### Documentation change already made

`docs/current_bugs.md` now contains a P0 follow-up investigation item under
poisoned summaries. It calls for structured-list preservation, keyed fact
extraction, summary quality/sentinel validation, raw-message retention when
lossy summaries are unsafe, and real-world manual regression cases.

### Do not assume fixed

Existing coverage tests can pass while this failure remains: they verify that
messages are structurally covered, not that the summary preserves exact facts,
indices, edits, or user-visible commitments. The manual session plan also
needs a semantic-fidelity case before summary work can be considered complete.

### Recommended next work (not implemented in this handoff)

1. Reproduce the 45-fact conversation and capture the generated summary and
   assembled prompt.
2. Define content classes that must not be summarized lossy (numbered lists,
   tables, exact mappings, code/config, and explicit user corrections).
3. Choose a preservation strategy: raw-message pinning, keyed extraction, or
   a hybrid summary plus retained source segments.
4. Add summary validation that rejects generic/refusal/omission summaries and
   checks required identifiers or sentinels.
5. Add manual and automated regression coverage, including a smaller-model
   comparison, then rerun the verification gate.

### 2026-08-23 — oversized-message trace follow-up

Investigated traces `f2713790-4be4-4b5c-9a30-137fb7113041`,
`0cc725d0-15a6-4296-8fb9-cf0bbb16b8b2`, and
`b73662e9-cd77-4cef-beab-d65fd8a0599b`. All belong to chat
`ca3a9bd67fef4addaec6b76d3417dc8a`, pinned to `coder-small`.

- The chat contains a 33,866-character first user message followed by
  `hey`. `coder-small` has a 6,144-token prompt budget after its 2,048-token
  output reservation; the estimated prompt was about 9,714 tokens, so both
  chat attempts correctly refused with `context_overflow` before calling the
  model.
- `f271...` persisted the first `hey` before context validation. The retry
  `b736...` persisted a second `hey` before failing, leaving duplicate user
  messages and no assistant response.
- The first overflow enqueued recovery summarization. `0cc...` sent the giant
  history to `utility-gpu` and timed out at exactly 180 seconds; no summary
  was committed. A follow-on trace `4db44341-1dd3-4fe7-8c28-3a0cd795ac06`
  was created for the fallback attempt but had no completed span at inspection
  time.
- `fit_to_budget()` still admits the first message when `kept` is empty, so
  an oversized document can be submitted whole to the summarizer. The live
  config also records that `utility-gpu` had known OOM/load failures, making
  this timeout consistent with the GPU summary path being unavailable.

This confirms a separate P0 lifecycle defect alongside summary semantic loss:
failed/context-overflow user turns are persisted repeatedly, and overflow
recovery submits an unsafe oversized message to the summary path. Fix scope
should cover atomic/conditional user-message persistence, retry behavior, and
a summary policy that refuses or specially handles oversized messages without
silently dropping the current request.

### 2026-08-23 — isolated context-capacity and Qwen3.8 screen (paused by user)

All llama-swap processes were stopped and each measurement used one direct
`llama-server` process, then tore it down. These are capacity/throughput
screens, not semantic long-context quality evaluations.

- `coder-small` (`qwen2.5-coder-7b`, GPU0): native context is 32,768;
  llama.cpp caps a requested 65,536 slot to that value. It sustained 112.3
  decode tok/s with 32,162 prompt tokens plus a 512-token completion. The
  shipped `ctx` is now 30,000, preserving response headroom while fixing the
  prior 8K rejection of ordinary ~10K-token documents.
- Qwen3.8 general/coder aliases (GPU0): the complete documented production
  flag set — thinking, reasoning preservation/budget 5000, MTP
  `ngram-mod,draft-mtp`, q4_1 KV, Flash Attention, and the configured sampler
  values — fails at 262,144 context. llama.cpp fails while MTP allocates its
  second 1GiB KV buffer. 131,072 boots with that full flag set; the Qwen3.8
  coder alias also booted at 196,608, but has no long-prompt decode evidence.
  A short production-style request at 131,072 measured 67.5 decode tok/s.
  Both `chat-default` and `coder` now ship at the conservative, validated
  131,072 rather than a configuration that cannot start.
- `utility-gpu` (`qwen3-8b`, GPU1): native context is 40,960, but a
  40,960-capable allocation consumes about 7.8GiB on the 8GiB card. It cannot
  coexist with the dispatcher there, so its live 16,384 limit must remain
  until a co-residency measurement proves a higher value safe.

Qwen3.8 quality screen (not a replacement verdict): with server-side
reasoning disabled, it returned schema-valid JSON for all 10 structured-output
cases. Its first six compile/run coding cases passed before that run was
stopped; the remaining cases were not executed, so do not report a full coding
score. A first reasoning attempt used only 4096 completion tokens while the
model's configured reasoning budget is 5000; it was stopped as invalid after
two correct cases (sheep 3.3s, train 28.8s). A corrected 5120-token,
131072-context run was started and then stopped at the owner's request before
recording a complete suite. No claim that Qwen3.8 replaces the dedicated coder
or DeepSeek reasoner is justified yet.

All isolated servers and evaluators were stopped before handoff; both GPUs
were empty. The sweep remains incomplete: vision, reasoner-alt, the CPU
utility/classifier/embed paths, full coding and reasoning comparisons, and
real long-context recall/fidelity tests still need role-appropriate runs. Do
not raise contexts from isolated capacity alone.

### Exact next-test matrix (do not infer a replacement decision yet)

The owner asked whether Qwen3.8 can replace all non-vision/non-utility roles.
That remains an open benchmark question, not a completed migration. The work
performed and the required follow-up are:

| Role / model | What was actually tested | What remains |
| --- | --- | --- |
| `chat-default` / Qwen3.8-27B | Full documented MTP runtime shape at 262K: boot failure (second MTP KV allocation OOM). Full shape at 131K: boot success. Reasoning-off structured JSON: 10/10 schema-valid. | Long-context prefill/decode and recall at 131K; general-chat transcript quality against the prior baseline; apply/regenerate and live-start validation after config change. |
| `coder` / Qwen3.8-27B alias | 262K MTP boot failure; 131K MTP boot + 67.5 short-request decode tok/s; 196K boot observed but not quality/performance tested. First 6 compile/run cases using Qwen3.8 general weights passed before the run was stopped. | Complete all coding compile/run cases and the six debug-diagnosis cases; compare them manually against the old dedicated Qwen3-Coder-30B-A3B 9/9 compile and 6/6 diagnosis results before replacing that role. |
| `reasoner` / Qwen3.8-27B | A 4096-output test began but is invalid for final comparison because model reasoning budget is 5000; two early cases were correct. Corrected 5120-output/131K test was started, then intentionally stopped before completion. | Rerun all seven fixed reasoning prompts with the full documented thinking flags, a >=5120 output allowance, and manual scoring against DeepSeek-R1-32B and the former Qwen3.6 baseline. |
| `reasoner-alt` / DeepSeek-R1-32B | Not tested in this sweep. | Capacity/throughput screen plus the same seven-prompt quality comparison. |
| `vision` / Qwen3-VL-32B | Not tested in this sweep; excluded from the Qwen3.8 replacement hypothesis. Historic Phase-0 result remains Qwen3-VL 6/6 versus Gemma 5/6. | Separate capacity test and rerun its six image-grounded prompts only if changing its context/runtime configuration. |
| `utility-gpu`, utility CPU, dispatcher, classifier, embed | Utility-GPU isolated native 40,960 allocation observed at ~7.8GiB; dispatcher coexistence was not tested. Dispatcher only had a llama-bench prompt/decode screen. CPU resident paths not tested. | Co-residency test for dispatcher + utility-GPU before any context increase; role-specific regressions for the CPU models. |
| `coder-small` / Qwen2.5-Coder-7B | Native 32,768 confirmed; 32,162 prompt + 512 completion achieved 112.3 decode tok/s. | Apply/regenerate and live-start validation for new 30K setting; optional long-context semantic/code-quality evaluation. |

No evaluator, isolated llama-server, GPU sweep, or verification command
should be treated as currently running after this handoff. The last requested
Python verification run was deliberately interrupted by the owner, so no full
pytest result is claimed for these documentation/configuration changes.

### Repository state

Branch: `main`. No application code was changed during this investigation.
`docs/current_bugs.md` is currently an untracked user audit file containing
the new note; preserve it when continuing. Recent relevant commits include
`1a340b5` (context integrity), `ae5e65e` (test/import repair and delayed-first-
token rewarm), and `2039672` (manual compaction progress UX note).

## 2026-08-15 — summarizer speed-aware budget + GPU1 fast path

Follow-on to the ctx-budget rewrite earlier the same day (see the two
entries below this one: the uncommitted `summaries.py` docstring, and the
6th-message-hang root cause). That rewrite made `_fit_to_budget` ctx-aware
but not *speed*-aware: it could build a bite that fits `utility`'s 8192 ctx
but that CPU can't actually finish inside a timeout. Confirmed live: the
2026-08-11 double-timeout incident (120s x2 retries) was exactly this —
`utility`'s old `max_tokens: 1024` output cap alone costs ~205s at measured
CPU decode speed, already exceeding any reasonable timeout before input
tokens are even counted.

**Live benchmark (ailab, real transcripts from `data/app.db`, chats
`eee45b51…`/`e211ca01…`/`c33ddccd…`), via `chat/completions` against
`utility` and `utility-gpu` directly, reading llama.cpp's own `timings`):**

| model | device | depth (prompt_tokens) | prefill tok/s | decode tok/s |
|---|---|---|---|---|
| utility | CPU (`--threads 8`) | 729 | 61.0 | 5.50 |
| utility | CPU | 1431 (719 cached) | 57.9 | 5.06 |
| utility | CPU | 2827 (1421 cached) | 53.4 | 4.78 |
| utility-gpu | GPU1 (3070) | 729 | 2446 | 73.3 |
| utility-gpu | GPU1 | 1431 (719 cached) | 2922 | 72.2 |
| utility-gpu | GPU1 | 2827 (1421 cached) | 2886 | 69.6 |
| utility-gpu | GPU1 | 6402 (2815 cached) | 2664 | 64.0 |

Rounded to `summary_cpu_tokens_per_sec_{prefill,decode} = 55.0, 5.0` and
`summary_gpu_tokens_per_sec_{prefill,decode} = 2700.0, 70.0` in
`BackgroundConfig` (`app/config.py`) — GPU1 decode is ~14x CPU.

**Design:**
- `app/background/summaries.py`: new `_time_budget_tokens()` derives a max
  input-token budget from `(summary_timeout_s - safety_margin -
  max_output_tokens/decode_tps) * prefill_tps`; `_fit_to_budget` now takes
  `min(ctx_budget, time_budget)`. `_run_summary` tries `utility-gpu` first,
  falls back to `utility` (CPU) on any `LLMError`/empty-content.
- `utility`'s `max_tokens` dropped 1024 → 512 (config.yaml, settings.local.yaml)
  — at 5 tok/s CPU decode, 1024 output tokens alone left a *negative* time
  budget, meaning the CPU path could never summarize anything under a
  reasonable timeout.
- New `background.summary_timeout_s` (180s) decouples the summary call's
  timeout from `llama_swap.timeout_s` (120s, tuned for interactive chat
  first-token latency) — a summary job is async/fire-and-forget, nobody is
  waiting on it. New `app.state.summary_llm_client` (separate `LLMClient`
  instance/timeout from `app.state.llm_client`) in `app/background/__init__.py`.
- New `utility-gpu` model entry (config.yaml, settings.local.yaml): same
  `qwen3-8b.gguf` as `utility`, `gpu: 1`, **resident:true, ttl:0** (always
  warm, like dispatcher/utility/embed/classifier already are — not a
  swap-on-demand entry). This requires `coder-small` to live on GPU0 (its
  default placement in config.yaml), not GPU1: measured live (nvidia-smi,
  8192 MiB 3070) dispatcher alone = 1303 MiB, + utility-gpu (ctx 16384,
  mid-generation) = 7253 MiB total, ~590 MiB free — no room left for
  coder-small (needs another ~3650 MiB) to coexist. The live overlay
  (`settings.local.yaml`) had actually put `coder-small` on GPU1 (`gpu: 1`)
  even though the checked-in `config.yaml` default is GPU0 — moved it back.
  Phase 0 rejected *permanent* co-residency of utility+embed alongside
  dispatcher on GPU1 for compute contention (5-7x dispatcher latency hit,
  `phase0-measurements.md` §8), but that was concurrent generation load
  from two always-busy models; a summarizer that's loaded-and-idle between
  rare token-pressure-triggered runs doesn't reproduce that — only VRAM
  residency does, which fits the 8GB card with just dispatcher.
- `app/gpu/swapgen.py`: two fixes needed to make `utility-gpu` possible.
  (1) `_select_entries`'s dedup key was file path alone — `utility` and
  `utility-gpu` share `qwen3-8b.gguf`, so the old key would have silently
  collapsed them into one entry (whichever won resident-priority). Changed
  the key to `(file, gpu)` — same file *and* same device is genuinely one
  swap slot (reasoner/chat-default); same file, different device is two
  separate processes. (2) Added a `gpu1-swap` group (`gpu==1 AND
  resident:false`, only emitted when non-empty) for future on-demand-GPU1
  models — not exercised by the current roster (`coder-small` is GPU0,
  `utility-gpu` is resident) but while investigating this, found the *live*
  deployed `llama-swap.yaml` had `coder-small` on GPU1 with **no group
  membership at all** (a real pre-existing gap — it fell into llama-swap's
  implicit default group, the exact defect swapgen.py's groups: block
  exists to prevent). Fixed as a side effect of getting `_select_entries`
  and the grouping logic right for this change.

**Verification:** `python -m pytest -q` (168 passed, includes new
`_time_budget_tokens`/GPU-fallback-routing/dedup-key/`gpu1-swap`-group
tests), `npx tsc --noEmit` clean. Live-applied via `/api/gpu/apply` +
`ai-mega-app.service` restart on ailab; confirmed via `nvidia-smi` and
`GET /v1/models` that `dispatcher`+`utility-gpu` both load without evicting
each other and `coder-small` loads cleanly on GPU0's `gpu0-main` group.

## 2026-08-15 — background jobs (title/summary) serialized behind each other — FIXED

User reported a "clog": util/summarizer/title/big-model calls seemed to
fight over "1 lane." Traced to `app/background/queue.py`'s `BackgroundQueue`
being a **single-worker FIFO** — both `titles.py` and `summaries.py` submit
jobs to the same queue, and the old `_run()` loop (`while True: item =
await self._queue.get(); await self._execute(item)`) processed exactly one
job at a time, system-wide, regardless of which model each job targeted.
So a title job (`dispatcher`, GPU1) and a summary job (`utility`, CPU) —
two entirely separate llama-server processes with nothing to contend
over — always serialized behind each other purely because of this queue's
design, not because of llama.cpp/llama-swap.

Confirmed the main chat completion (the "big AI model") is NOT routed
through this queue — it's awaited directly in the SSE request handler — so
it isn't blocked by title/summary jobs at the code level; the perceived
"big model" clog is more likely the CPU-thread-contention issue fixed
earlier the same day (`e13718e`), not this queue.

**Fix:** rewrote `BackgroundQueue` to run each submitted job as its own
`asyncio.Task` (tracked in a `set` for `stop()`/`cancel()`/`drain()`)
instead of a single consumer loop over an `asyncio.Queue`. Title and
summary jobs (and any future background job type) now genuinely run
concurrently. Kept the same public contract (`submit`/`start`/`stop`/
`cancel`/`drain`) and per-job "one retry, then drop" semantics so
`app/background/__init__.py` and all existing tests needed no changes
beyond the queue implementation itself. SQLite access under concurrent
jobs is safe as-is — `app/db.py`'s `connect()` already sets WAL mode +
`busy_timeout=5000`, and `run_sync` offloads each blocking call to the
default thread-pool executor, so concurrent writers retry instead of
erroring.

**Deferred (user chose not to do this now):** per-model `--parallel`/`-np`
slots on llama-server so a *single* model can serve more than one
concurrent request — every model in `config.yaml`/`swapgen.py` currently
gets llama.cpp's default single slot. This is a separate, real bottleneck
(e.g. two concurrent calls to the same `classifier` instance still queue
inside llama.cpp itself) but costs extra VRAM/RAM per slot (KV cache
duplicates per slot) and needs a per-model decision on slot count — logged
here, not implemented.

**Verification:** `pytest -q` → 157 passed (including the 8s
shutdown-drain-timeout test in `test_background.py`, which still passes
since `stop()`'s bounded-wait/cancel behavior is unchanged). `npx tsc
--noEmit` clean. No config/schema/SSE changes — pure Python, no restart
strictly required, though a restart is already pending for the earlier
CPU-threads/warmup fixes today.

## 2026-08-15 — title-gen still echoing the exchange verbatim — ROOT CAUSE FOUND (dispatcher, not the classifier), FIX COMMITTED, NO RESTART NEEDED

User re-reported "title generation and/or classifier behavior is still
broken" despite prior sessions' fixes (a37fc1c truncation, 3668ee1 span
recording, a43d9c3 punctuation strip). Scope: title-gen + classifier only
(model-switch UI and the summarizer timeout are separate concurrent
agents' work this session — see the entry below and `docs/FIX_PLAN_2026-08-11.md`).

**Classifier: confirmed healthy, not the bug.** `routing.classifier.timeout_s:
90.0` is live in both `config.yaml` and `settings.local.yaml` (the 6.0->90.0
fix already landed). `orchestrator.py:318` still calls `_route(...,
llm_client=self.llm_client, config=self.config, ...)` — the 2026-08-02
silent-fallback regression has not recurred. Queried `data/app.db`'s `spans`
table directly (`stage='route'`): the one non-override route in the
retained window shows `source: 'classifier'`, `confidence: 0.97`,
`latency_ms: 2562` — real classification, not a fallback. (Most other route
spans are `source: 'override'` because the user has been manually pinned to
`coder-small`, which is expected behavior, not a bug.)

**Title-gen: real bug, still live.** Read all 11 recent `stage='title'`
spans out of `data/app.db` directly. About half were fine ("Python Script:
Printing Numbers 1 to 100...", "Today's Weather Forecast"), but the rest
were the dispatcher model (Hammer2.1-1.5B, a tool-calling model, not a
generative one) echoing the exchange almost verbatim instead of
summarizing it:
- raw `"Meow! How are you doing? Meow!"` -> stored title `"Meow! How are
  you doing? Meow"` (word-for-word copy of the assistant's own reply)
- raw `"Chat: Hello! It sounds like you might be trying..."` -> stored
  title `"Chat: Hello! It sounds like you"` — model also leaked a
  `"Chat:"` preamble that `clean_title` only stripped for `"Title:"`
- raw `"<project_instructions>"` -> stored title `"<project_instructions>"`
  verbatim — a persona/project-instructions block that this user's chats
  inject as literal `user` message content got fed straight into the
  title prompt and echoed back untouched (separate, narrower issue —
  noted below, not fixed this session)

Root cause: the a37fc1c truncation fix only stops echoing of *long*
replies (nothing to truncate on a short one), so short/casual exchanges
(chit-chat greetings) still slip straight through unchanged, and
`clean_title` had no check for "is this actually a summary or just a
restatement."

**Fix (`app/background/titles.py`):**
1. `clean_title` now also strips a leaked `"Chat:"`/`"Summary:"` preamble
   (previously only `"Title:"`), regex generalized to
   `^(?:title|chat|summary)\s*:\s*`.
2. New `is_echo(title, *sources)` — normalizes to lowercase alnum words
   and flags a title whose first ≤6 words exactly match the opening words
   of the user or assistant text it was generated from. Deliberately
   requires a full-prefix match (not just topic-word overlap) so a
   legitimately synthesized title that happens to open with 2-3 similar
   words (e.g. "The RTX 3090's VRAM Budget for..." vs a reply that also
   opens "The RTX 3090's 24GB VRAM...") is not falsely flagged — verified
   against that exact live pair in `tests/test_background.py`.
3. `_first_exchange` now also returns the raw (untruncated) user/assistant
   text alongside the formatted prompt string; `_run_title_job` runs
   `is_echo` against both, and on a match writes a deterministic
   fallback title (`_fallback_title`: first 6 words of the user's own
   message, same punctuation cleanup as `clean_title`) instead of the
   echoed garbage. The `title` span now also records `echo_detected` for
   Debug visibility.

Tests added to `tests/test_background.py`: `test_clean_title_strips_chat_preamble`,
`test_is_echo_detects_verbatim_restatement`, `test_is_echo_detects_leaked_user_tag`,
`test_is_echo_allows_similar_but_distinct_titles`, and an end-to-end
`test_echoed_title_falls_back_to_deterministic` through
`on_turn_complete`/queue drain. Full gate: `pytest -q` 157 passed,
`npx tsc --noEmit` clean.

**Not fixed this session (noted for whoever picks it up):** the
`<project_instructions>` leak is a distinct, narrower bug — this user's
chats apparently inject a persona/project-instructions block as literal
`user`-role message content (rather than through a system-prompt channel),
and `_first_exchange` picks up whatever the *first* user message in the
chat is, instructions block included, with no awareness that it isn't real
conversation content. `is_echo` will now stop it from becoming the literal
stored title (falls back to a truncated echo of that same block instead,
which is still not a good title), but the actual fix belongs wherever
project instructions get woven into message history — outside this
session's file scope (`app/chat/orchestrator.py`'s project-instructions
handling / wherever project docs get injected, not the routing/classifier
call sites this session was scoped to touch).

**Deploy:** no restart needed — pure Python logic change in a background
job, no config/schema/SSE changes.

## 2026-08-15 — summarizer timeout (session-5 continuation) — FIXES VERIFIED, ONE COMMITTED, LIVE RESTART STILL NEEDED

Continuation of the 2026-08-11 session-5 entry ("6th-message hang"). User
still reported the rolling summary as "not working." Picked up the two
unfinished threads from that entry plus an interrupted-session diff sitting
in the working tree.

**1. Warmup-storm fix (item 4 of the session-5 entry) — already landed.**
Checked `app/warmup.py`/`app/main.py::_warmup_loop` against the description:
`loaded_resident_names()` and the `skip` param on `warmup_resident_models()`
are both present and wired into `_warmup_loop` (only re-pings stragglers
during the startup phase). This is commit `f75d812` ("fix: skip re-pinging
already-loaded residents during startup warmup"), already on `main`. Per
the session-5 entry this was written but **not yet deployed** as of
2026-08-11 (needed a restart the user hadn't approved) — status unchanged;
still needs `sudo systemctl restart` + live re-trace to confirm the ping
storm actually stops and the summary job's `utility` call stops timing out
in practice.

**2. Uncommitted diff found in the working tree — verified and committed.**
Two changes were sitting uncommitted, apparently from an interrupted prior
session, both in scope for this bug:
- `app/background/summaries.py`: adds `prompt`/`response` fields to the
  `summary` debug span, mirroring the already-committed title-span fix
  (`3668ee1`). Reviewed — correct, low-risk, makes summary failures/timeouts
  actually inspectable in the Debug panel instead of opaque `chars`-only
  spans. Kept.
- `config.yaml`/`settings.local.yaml`: caps `--threads` on the three
  CPU-resident models (`utility: 8`, `embed: 4`, `classifier: 4`, out of 32
  cores). Rationale in the `config.yaml` comment: uncapped CPU inference
  was grabbing most/all cores during a summary run, starving concurrent
  CPU-resident calls (classifier route lookups, embed) — a second,
  independent contention path from the warmup-storm one, since it can
  happen any time a summary and a classifier/embed call overlap, not just
  during the post-restart warmup window. Verified `--threads` is a real
  `llama-server` flag (`llama-server --help`), confirmed `app/gpu/swapgen.py`
  already forwards `extra_flags` generically (no swapgen code change
  needed), and confirmed `tests/test_swapgen.py`'s golden `cmd:` strings for
  `utility`/`embed`/`classifier` were updated to match. `pytest -q` (152
  passed) and `npx tsc --noEmit` both clean with the change in.
  Judged sound and complementary to the warmup fix (that fix prevents the
  storm from starting after a restart; this one bounds the blast radius if
  CPU contention happens for any other reason) — committed both.
- Left one unrelated hunk in `settings.local.yaml` (`ttl_s: null -> 0` on
  the `coder` model, lines 55-61) uncommitted/unstaged — it was mixed into
  the same working-tree diff but isn't a utility/embed/classifier/dispatcher
  threads-cap change and isn't described anywhere in the session-5 or
  interrupted-session context, so it wasn't reviewed here. Still present
  in the working tree for whoever owns it.

**Not independently re-reproduced live** (no GPU-box restart run this
session, per instructions) — the fix is code+config verified and
test-passing, but the actual timeout-under-contention behavior can only be
confirmed post-restart with a live 6-turn chat, same as the session-5 entry
already said. Next session: get the restart approved, replay a 6-message
chat, confirm the `summary` span completes without `timeout` and check its
new `prompt`/`response` fields render sensibly in the Debug panel.



User reported model-switch is "still broken" despite the session-3
`d6a6dd0` fix (hydrate picker from `chat.model_override` on open) and the
override-resolution logic in `orchestrator.py:298-310` (explicit `model` or
`chat_row["model_override"]` always wins, source `"override"`) both already
being on `main` and passing their tests. Confirmed both of those really are
in place and correct — this is a different, previously-undiscovered gap in
the same feature, only visible in one specific sequence:

**Repro:** open a brand-new chat (no `chatId` yet) → pick a model from the
picker *before* sending anything → send the first message → the response
correctly comes from the picked model (looks fixed!) → navigate away and
back to the chat, or reload the page → picker shows "Auto" again and the
next message silently goes back through classifier routing.

**Root cause:** `applyModelPick()` (`web/src/views/composer.ts:103-114`)
only calls `setChatModel()` (persists to `chats.model_override` via `POST
/api/chats/{id}/model`) when a `chatId` already exists — correct, since
there's no chat row to persist to yet on the "New chat" screen. But nothing
ever went back and persisted that pending pick once the chat *was* created.
`ensureChat()` (`web/src/views/chat.ts`, was ~line 268) created the row via
plain `createChat()` (no model) and `send()`/`regenerate()` pass
`model: get().modelOverride` straight through in the per-request SSE body
— so `orchestrator.handle_message`'s `model` param carries the pick and the
*first* turn resolves correctly — but `chat.model_override` in SQLite stays
NULL forever. Any later re-hydration (`chat.ts` mount: `modelOverride:
chat?.model_override ?? null`, the exact code path session 3 added) reads
that NULL and silently resets to Auto. Reads exactly like "my model pick
didn't take" even though it did, for one turn, then quietly reverted.

**Fix (`web/src/views/chat.ts::ensureChat`):** after creating the chat row,
if `store.modelOverride` is already set (a pre-send pick), call
`applyModelPick(id, pending)` to persist it before returning — same
`setChatModel` path the existing picker-on-an-open-chat flow already uses,
just also triggered retroactively at the moment the chat starts existing.

Rebuilt `web/js/views/chat.js` (`npx tsc`, committed with the source per
CLAUDE.md's "one TS module, plain tsc" rule). No backend/config files
touched (this was believed to possibly need a config/backend fix per the
sticky-routing hypothesis in the task brief, but sticky routing only
applies on the *classifier* path — the override path bypasses it entirely
and was already correct; the bug was purely a frontend persistence gap).
Verified: `.venv/bin/python -m pytest -q` 152 passed, `npx tsc --noEmit`
clean. This is a static-file-only change — no `sudo systemctl restart`
needed, should go live on next static asset refresh/deploy.

## 2026-08-11 — session 5: 6th-message hang ("re-loads a model already loaded") — ROOT CAUSE IDENTIFIED, FIX WRITTEN BUT NOT DEPLOYED, hand to a more powerful model for full debug

**User-reported symptom (live, casual chat):** every ~6 messages into a
conversation, the app appears to reload a model that was already loaded and
the UI hangs. User asked for this to be logged for a deeper debug pass by a
more powerful model — this entry has everything gathered so far so that
session doesn't have to re-derive it.

**What's actually confirmed so far (this session), likely explains it fully
but NOT yet live-verified post-fix:**

1. `background.summary_every_n_turns` is `6` (`config.yaml:239`) — the
   rolling-summary job (`app/background/summaries.py::maybe_enqueue_summary`)
   fires exactly on the 6th user turn (`turn_count % every_n == 0`), and
   again at 12, 18, etc. This lines up exactly with "after 6 messages."
2. Root-caused via live trace inspection (chat id
   `90beebb7454f4a659ab4a4aadcdde0d1`, "The RTX 3090's VRAM Budget for"):
   at that chat's 6th user turn, the summary job's call to `utility`
   **timed out after 120s, twice** (initial attempt + the one automatic
   retry `summaries.py` allows) — confirmed via the `summary` span's stored
   `error: "LLMError('timeout: ')", duration_ms: 120008` in both attempts.
3. Cause of the timeout: `app/main.py::_warmup_loop`'s startup-phase retry
   was stuck re-pinging **all 5** resident models (`chat-default`,
   `dispatcher`, `utility`, `embed`, `classifier`) with real inference
   completions every 15s, for ~10 minutes after a restart, because
   `all_residents_loaded()`'s `/v1/models` status check kept lagging behind
   the actual ping results and never converged. This is very likely what
   reads to the user as "it reloads a model already loaded" — the warm-up
   loop genuinely does keep re-issuing load/ping traffic to already-loaded
   models, visible as repeated activity for models that shouldn't need it
   again. That ping storm was competing for the same CPU cores as the
   summary job's own call to `utility` (also CPU-resident), starving it.
4. **Fix written this session** (`app/warmup.py`: new
   `loaded_resident_names()` + a `skip` param on `warmup_resident_models()`;
   `app/main.py::_warmup_loop` now checks which residents are already
   loaded before each sweep and only re-pings the stragglers). `pytest -q`
   (152 passed) and `npx tsc --noEmit` clean. **NOT YET DEPLOYED** — needs
   `sudo systemctl restart ai-mega-app` (user hadn't approved the restart as
   of this session's end).
5. Separately observed same session, possibly related: a `route` span on
   this same chat took **3694ms**, almost entirely the classifier call
   (`classifier_ms: 3692`) — well above the documented ~0.9-1.1s warm
   baseline. Plausibly the same warm-up-storm contention; not independently
   confirmed. Worth re-checking after the warmup fix is deployed — if route
   latency is still spiking to seconds post-fix, that's a distinct bug.
6. Also separately observed same session (probably unrelated to the hang,
   but worth the next session having it): `chat-default` has `thinking:
   false` but `reasoning_off: false` in `config.yaml`/overlay. Since it's
   Qwen3.6 (hybrid-reasoning capable), it self-triggers a full internal
   `<think>` block regardless — one live trace showed 1925 total completion
   tokens, only 683 of them the actual visible answer (1242 tokens of
   invisible reasoning), inflating a response to 13.4s. Per `CLAUDE.md`'s
   own frozen rule, only llama-server's `--reasoning off` flag reliably
   suppresses this, not the `thinking` request flag alone. Not fixed —
   user was deciding whether to flip `reasoning_off: true` for
   `chat-default` when this session ended.

**What's NOT yet confirmed:** whether the warmup-storm fix (item 4) is
actually the *entire* explanation for "hangs after 6 messages," or just a
major contributor. The next session should: (a) get the restart approved
and deployed, (b) live-reproduce a 6-message chat and confirm the summary
job completes without timing out and the route/classifier latency stays
near baseline, (c) if the hang still reproduces post-fix, look harder at
whether `POST /api/gpu/apply`-triggered reloads or something in the
swap-aware routing (`orchestrator.py`, prefers currently-loaded GPU0 model)
could itself be forcing a real swap around turn 6 for a reason unrelated to
the background jobs. Don't assume item 4 alone is the full fix until
live-reproduced.

## 2026-08-11 — session 4: `reasoner-alt` (DeepSeek-R1) enabled, stale GGUF filename fixed — LIVE-VERIFIED

User switched a chat to `reasoner` expecting DeepSeek-R1 and saw no
load banner + assumed something was broken. Two separate things
clarified/fixed:

**1. `reasoner` is not DeepSeek — this is by design, not a bug.**
`reasoner` (`config.yaml:39-54`) is the same Qwen3.6-35B-A3B blob as
`chat-default`, thinking enabled at the request layer (comment already
says this). Since `chat-default` was already loaded when the user
switched, no swap occurred — that's why there was no loading banner.
The actual DeepSeek-R1-Distill-Qwen-32B model is `reasoner-alt`, which
was `enabled: false` by default (`PLAN.md` §4.1: off by default because
Phase 0 measured 3/6 debug-diagnosis with confidently-wrong fixes,
despite 7/7 on plain reasoning).

**2. Real bug found while enabling `reasoner-alt`: stale GGUF filename.**
`config.yaml`/`settings.local.yaml` both pointed `reasoner-alt.file` at
`DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf`, which doesn't exist on disk
— confirmed via `ls`. Actual files at `/home/john/llm-stack/models/gguf/`
are `deepseek-r1-32b.gguf` and `deepseek-r1-32b-16k.gguf` (both 19.8GB,
dated Jul 14 — a rename happened at some point after Phase 0 and
`config.yaml` was never updated). This means `reasoner-alt` would have
404'd or failed to load even if someone had flipped `enabled: true`
before now. Picked the plain (non-`-16k`) file to match the existing
`ctx: 8192` setting.

**Fix:** `config.yaml` — corrected `file:` path only, left
`enabled: false` (shipped default stays off per the Phase 0 decision,
golden tests in `test_swapgen.py` assert this). `settings.local.yaml`
(user override) — corrected `file:` path AND `enabled: true`, so
`reasoner-alt` is live for this user without changing the repo default.
`pytest` gate confirmed: flipping `enabled` in `config.yaml` itself
breaks 3 golden swapgen tests (`test_golden`,
`test_reasoner_alt_disabled`, `test_gpu0_main_membership`) — the overlay
is the correct layer for a per-user enable, not the checked-in default.

**Live-verified after user restarted `ai-mega-app` + `POST /api/gpu/apply`:**
`reasoner-alt` appears in the regenerated `llama-swap.yaml`'s
`gpu0-main` swap group (`cmd: ... -m .../deepseek-r1-32b.gguf ... -c
8192`, `CUDA_VISIBLE_DEVICES=0`). Direct `curl` to
`/v1/chat/completions` with `model: reasoner-alt` returned a real R1
reasoning trace in `reasoning_content` (~14s cold load, ~38 tok/s
generation) — confirms the fix works end-to-end, not just config-valid.

**Note for later:** `get_config()` (`app/config.py:209-216`) caches the
merged config in-process and only invalidates via
`reset_config_cache()`, which Settings-UI writes call but a direct
hand-edit of `settings.local.yaml`/`config.yaml` does not — any manual
YAML edit needs a full service restart before `/api/gpu/apply` will
pick it up, not just the apply call alone. Hit this live this session:
first `apply` after editing the overlay silently used the stale cached
config (missing `reasoner-alt`, and oddly also missing `coder-small`
from `gpu0-main` — unexplained, worth checking next time it's
reproducible) until the user restarted the service.

## 2026-08-11 — session 3: override hydration, live-refresh, scroll, summary, timeout — all FIXED

Sixth round of live bugs this day, reported in one batch. All fixed and
committed to `main`; JS fixes are already live (static files, no restart
needed), Python/config fixes need `sudo systemctl restart ai-mega-app`.

**1. Model-picker "Auto" vs persisted override — FIXED (`d6a6dd0`).**
Was logged as a TODO earlier this session (see below); fixed now.
`ChatSummaryOut` + `list_chats` now include `model_override`; chat.ts
hydrates `store.modelOverride` (and the picker label) from the opened
chat's row instead of always defaulting to null/"Auto".

**2. Title/summary never appear without a manual refresh — FIXED (`d08ce71`, `d79dba4`).**
Two compounding bugs: (a) `app.state.emit_chat_sse` (what
`_emit_title_sse` needs to push a live title update) is never set
anywhere in `app/` — always a no-op — and is structurally unreachable
anyway since the per-request SSE stream closes at `done`/`error` before
the background title/summary job even starts. (b) the one `listChats()`
refresh right after `done` only had a shot at catching the ~1-1.5s title
job, never the summary job (17-40s on CPU `utility`, every
`background.summary_every_n_turns` turns) — confirmed live: the "today
weather forecast" chat's `chats.summary` WAS correctly generated
server-side, just never fetched into the client in time. (c) even when
fetched, nothing in `chat.ts` listened for store changes to re-render
the summary banner — only the sidebar (`app.ts`) subscribed. Fixed:
staggered `refreshChatsSoon()` refetches (0/2.5s/10s/30s/50s after each
turn) plus a `chat.ts` store subscription that re-renders the summary
banner live.

**3. Auto-scroll stuck at top for an entire new-chat stream — FIXED (`2dfb95d`), live-reproduced with Playwright/Firefox.**
`renderMessages`' double-rAF scroll guard captured "was near bottom"
before the DOM update, then re-derived "still near bottom" from the
**new, taller** `scrollHeight` against the still-stale `scrollTop` —
streaming growth alone pushes `scrollHeight` well past the 20px
threshold on nearly every chunk, so that check almost always failed and
permanently pinned the view at its pre-stream scroll position. Repro:
`scrollTop` stuck at 0 while `scrollHeight` grew 524px→3364px over a
stream (script since deleted, was `scroll_repro.js` at repo root — used
Firefox via Playwright per the existing tooling note further down this
file). Fixed by comparing `scrollTop` against its own value at update
time (did the *user* scroll) instead of re-deriving from content growth.
Verified fixed with the same repro script post-fix (scrollTop tracked
scrollHeight throughout, ~0-150px from bottom).

**4. `first_token_timeout` stuck mid-conversation on a GPU0 swap — FIXED (`f0a5936`, owner-approved config bump).**
Trace `e14cbbe6` (2026-08-11): `swap_wait` alone measured **28s** for
`chat-default`'s GPU0 slot swap-in — the old `first_token_timeout_s: 30`
(PLAN.md §4.1's 12.47s cold-load benchmark, long stale) left ~2s for the
model to actually produce a token. Bumped to 90s, config.yaml only
(mirrors the classifier timeout bump earlier this session, same
underestimate class of bug). **Needs a restart to take effect.**

**5. Also found and fixed opportunistically while chasing #2 above:**
title-gen echoing the assistant's reply verbatim instead of summarizing
(`a37fc1c`, exchange truncation), title spans not recording
prompt/response for Debug (`3668ee1`), dangling trailing punctuation
after title word-count truncation (`a43d9c3`), and **test pollution of
the live production database** (`affea51`) — `tests/test_gpu_inventory.py`'s
bare-FastAPI-app tests never isolated `app/debug/trace.py`'s
lazily-opened global DB connection, so every local `pytest` run on this
box was writing test spans straight into `data/app.db` (a stray
`test_apply_no_existing_file...` swapgen trace surfaced this, spotted by
the user in the live Debug view). Fixed with an autouse
`_isolate_debug_trace` fixture in `tests/conftest.py`; 8 polluted rows
cleaned out of `data/app.db` by hand.

**General lesson for this whole session:** almost every live bug traced
back to one of two patterns — (a) a background job's result only ever
reaching the client via a *single* opportunistic refetch that assumed
the job would finish faster than it actually does, or (b) a module-level
lazily-initialized global resource (DB connection, logging config) with
no forcing function to keep it isolated/configured in every context that
touches it. Worth grepping for both patterns elsewhere before the next
live test pass.

## TODO — persist per-message usage/timing/trace so it survives a reload

`assistant.traceId` / `tokensPerSecond` / `promptTokens` / `completionTokens`
(`web/src/composer.ts` `ChatMsg` type) only ever exist in the in-memory
object populated from the `done` SSE payload — `messages`/`MessageOut`
never persists them, so a page reload has nothing to reconstruct them
from ("trace and token count disappear after refresh", reported live
2026-08-11). Needs a SQLite schema change (new `messages` columns or a
sibling table) — per CLAUDE.md that's an "ask first" item, not done this
session. Fix shape: persist `model`/`trace_id`/`usage`/`timings` on the
assistant message row in `orchestrator.py`'s `db: persist_assistant_message`
span, add the fields to `MessageOut`, and read them back in `loadHistory`.

## TODO (RESOLVED same day, see "session 3" entry above, commit `d6a6dd0`) — model-picker shows "Auto" even when a chat has a persisted manual override

User-reported live: Debug showed `route` span `source: "override", intent: "manual"`
(chat locked to `coder-small`) for a chat whose composer UI still displayed
"Auto (router)". Root cause (not yet fixed, just diagnosed):
- `ChatSummaryOut` (`app/chat/api.py:34`) has no `model_override` field at
  all — `GET /api/chats` never tells the frontend a chat has one.
- `web/src/store.ts`'s `modelOverride` is pure client-side state
  (`null` on init/every page load); nothing in `web/src/views/chat.ts`
  (`loadHistory`, mount) ever hydrates it from the chat being opened.
- So: pick a model manually → `POST .../model` persists `model_override`
  on that chat row (`app/chat/history.py::set_model_override`, correctly
  enforced server-side via router Layer 1, `app/router/router.py:152-163`)
  → reload the page, switch chats, or restart the app → picker silently
  resets to "Auto" while the backend keeps routing that chat through the
  override. Not a state-loss-on-restart bug specifically — it's structural:
  the override was never round-tripped to the client in the first place.
- **Fix shape (not yet done):** add `model_override: str | None = None` to
  `ChatSummaryOut` + populate it in `app/chat/history.py::list_chats`'s
  SELECT; on chat open, `set({ modelOverride: chat.model_override })`
  before/alongside `loadHistory`; update `selectedModelLabel()`'s caller
  to re-render after that hydration.

## 2026-08-11 — session: warmup fix, title-gen fix, title span prompt/response — FIXED, live-verified

Follow-up session after Wave 1 merge, working through open HANDOFF items live on `ailab`.

**1. Title-gen echoing the assistant's reply instead of a title — FIXED (`a37fc1c`).**
Live repro: chat "hello who are you" → long Qwen self-intro got titled
`"Hello! How can I assist you"` — the `dispatcher` model echoed the tail
of the long assistant reply instead of summarizing it. Root cause:
`_TITLE_PROMPT`'s few-shot examples (`app/background/titles.py`) are two
short sentences each; real replies (code blocks, multi-paragraph answers)
don't match that shape and confuse the small title model into continuing
the conversation rather than titling it. Fix: `_first_exchange` now
truncates each side of the exchange to 400 chars before building the
prompt. Live-verified after redeploy: a new chat titled correctly
("List Files in a Directory") on the first try.

**2. Title spans showed "no prompt/response recorded" in Debug regardless of `debug.store_prompts` — FIXED (`3668ee1`).**
`debug.store_prompts` was already `true` in `config.yaml:187` — the real
bug was `titles.py` setting `raw`/`title` span fields, not the
`prompt`/`response` keys `web/src/views/debug.ts:216-224` specifically
looks for. Added `sp.set(prompt=prompt, response=raw, ...)`. Not yet
live-verified (needs a restart + one more title job to confirm the tabs
populate) — do that next session before treating this as fully closed.

**3. `post_apply` warmup hardening (`30ee188`, defense in depth, not the fix that mattered)** — see item below.

**4. Test pollution of the LIVE production database — FOUND LIVE, FIXED (`affea51`).**
User spotted a stray `swapgen` trace in the live Debug view pointing at
a `.pytest-tmp/run/test_apply_no_existing_file_st0/...` path — a test
span, in production data. Root cause: `app/debug/trace.py`'s
`_connection()` lazily opens a module-global connection to
`config.db.path` (real `data/app.db` by default) on first use; any test
building a bare FastAPI app + router without setting `app.state.db` or
calling `reset_debug_connection` (`tests/test_gpu_inventory.py`'s
`_make_app` did exactly this) falls through and writes straight into
whichever DB happens to be live at the time — the real one, when run on
`ailab` outside a fully-isolated CI box. Fixed with an autouse
`_isolate_debug_trace` fixture in `tests/conftest.py` (mirrors the
existing `_isolate_overlay` pattern) that points every test at its own
tmp DB by default. The 8 polluted trace rows this session's own pytest
runs had already written to `data/app.db` were deleted by hand.
**General lesson: any module with a lazily-opened global resource
(`_conn`, similar patterns elsewhere) needs an autouse test fixture
forcing isolation, not per-test opt-in — an opt-in pattern silently
reverts to touching production the moment a new test forgets to call
the reset hook.**

## 2026-08-11 — bug: warmup not leaving resident models loaded after deploy — FIXED

**Root cause confirmed: hypothesis 1 (INFO logs invisible) was the whole story — the warmup loop was working correctly the entire time, just unobservable.**

Fix (`30ee188`): added `logging.basicConfig(level=logging.INFO, ...)` in `app/main.py` — uvicorn's default logging config (dictConfig) only sets up the `uvicorn`/`uvicorn.error`/`uvicorn.access` loggers, never the root logger, so it stayed at default WARNING and silently dropped every `app.*` `logger.info(...)` call, including the warmup loop's own observability. Also hardened `app/gpu/api.py::post_apply` to retry its post-apply warmup up to 3x (checking `all_residents_loaded`) instead of a single shot, since a reload-race ("group is shutting down") can still hit the first attempt.

**Live verification (2026-08-11 20:00, after `POST /api/gpu/apply` + `sudo systemctl restart ai-mega-app`):** journalctl now shows the full startup sweep — `warm-up starting`/`warm-up complete` for chat-default, dispatcher, utility, embed, classifier, then `warmup loop: all residents loaded, switching to steady-state (300s interval)` at 20:00:05, ~7s after restart. `curl :8080/v1/models` confirms all 5 residents `loaded`; coder/coder-small/vision correctly `unloaded` (non-resident swap group). Interestingly, the *previous* apply's post_apply warmup attempt (19:59:35) did hit the "group is shutting down" race on all 4 CPU/GPU1 residents — exactly hypothesis 2 — but the restart's own startup sweep recovered them cleanly regardless, so the retry hardening is defense in depth, not what fixed this particular symptom.

**Original report below, kept for context:**

Config regeneration is CORRECT (verified live): every `env:` pair now has
`CUDA_DEVICE_ORDER=PCI_BUS_ID` beside `CUDA_VISIBLE_DEVICES`, `resident` group =
`[dispatcher, utility, embed, classifier]`, `gpu0-main` = `[chat-default, coder,
coder-small, vision]`. `/home/john/llm-stack/serving/llama-swap/config.yaml`
matches `git` swapgen golden output. Services `active`.

**Symptom:** after `POST /api/gpu/apply` + `sudo systemctl restart ai-mega-app`,
`/v1/models` shows the CPU/GPU1 residents (dispatcher, utility, embed, classifier)
UNLOADED, and they stay unloaded over time — only `chat-default` comes up loaded.
Manually `curl`ing a `chat/completions` to `classifier` loads it immediately and
subsequently shows `loaded`, so llama-swap + config + GPU pinning are healthy;
the warmup loop is not doing its job.

**Root-cause hypotheses (investigate next session):**
1. **INFO logs invisible (confirmed).** No `logging.basicConfig`/`setLevel` anywhere
   in `app/` — Python default WARNING level suppresses all `logger.info(...)`
   output. So `warmup.py:60` "warm-up starting", `main.py:76` "warmup loop:
   starting sweep", and `main.py:80` "sweep complete" never reach journalctl.
   Old PID's `warm-up failed (...)` lines DID show because they're WARNING.
   → Means the warmup loop's only observability (WS-B's logging fix) is dead in
   production. Add logging config (INFO for `app.*`) or raise these to WARNING.
   This alone doesn't explain residents staying unloaded, but it hides whether
   the loop runs.
2. **`post_apply` single-shot warmup races llama-swap reload (likely).**
   `app/gpu/api.py:145` calls `warmup_resident_models(...)` immediately after the
   health poll. llama-swap's `-watch-config` reload kills all llama-servers; at
   that instant requests can fail with `{"error":"unspecific error: group is
   shutting down"}` (seen at 19:41). It is a single pass, NOT a retry — so if it
   fires mid-reload, residents stay cold with no recovery until the 300s sweep.
3. **Startup retry loop not actually warming (need to confirm).**
   `_warmup_loop` (main.py:57-88) retries every `_STARTUP_BACKOFF_S` (15s) while
   `all_residents_loaded()` is False, else settles to 300s. Wait-then-`/v1/models`
   after restart showed residents still unloaded >20s later → suggests the loop is
   NOT resolving them, or `app.state.llm_client`/`config` weren't picked up, or the
   loop died on a startup-phase exception (hidden by suppressed INFO + only
   WARNING exposure). Verify the task is alive and `all_residents_loaded` logic
   against live `/v1/models` shape (`status.value`).

**Deploy commands used (live):** `curl -X POST http://localhost:8000/api/gpu/apply`
then `sudo systemctl restart ai-mega-app`. Note: first apply was run BEFORE restart
(i.e. old code) → produced stale config missing `CUDA_DEVICE_ORDER`; re-ran apply
after restart → correct config. Verify config, don't assume.

**Verification note:** `python` on box = `.venv/bin/python` (repo root `.venv`).

**Related:** the whole point of WS-B (`fix/backend-reliability`, commit 86d6cf5 /
merged 0c8ac3d) was "residents hot at service start" — this deploy shows that goal
is not met in production. Treat as the top open item.

## Where things stand (2026-08-11 — post-audit fix plan)

Full audit completed; see `docs/AGENT_CONTEXT_MEGA.md` (facts, code-verified) and `docs/FIX_PLAN_2026-08-11.md` (execution). Four parallel workstreams in flight, one worktree each, disjoint FILE SCOPEs, all forked from `main` at `0170ca4`:

- **WS-A fix/config-drift** — swapgen emits `CUDA_DEVICE_ORDER=PCI_BUS_ID` alongside every `CUDA_VISIBLE_DEVICES=N`; revert `settings.local.yaml` drift (coder-small residency/ttl_s, routing overlay attachments + code_task rules, classifier timeout 6→90s); settings.json legacy ollama tags → -16k/-24k.
- **WS-B fix/backend-reliability** — classifier/utility hot at service start (warmup retry + stored task ref on `app.state`); `_on_turn_complete` on error/timeout paths; `reasoning_content` field on `ChatDelta` + parse in `llm_client` (no new SSE events — frozen vocab).
- **WS-C fix/web-gaps** — retry affordance on error banner; response time + usage inline in `.msg-meta`; fix misleading `chat.ts:35-36` comment; real `npx tsc` build committed with `web/src/**`.
- **WS-D docs/refresh** (this branch) — refresh stale "Phase 1 open / web unbuilt" prose in `AGENTS.md`/`CLAUDE.md`; add this HANDOFF entry.

**Items audited FIXED in tree (do not redo; commits `8c4f7b4`, `53dac3a`, `0170ca4`, `96602e4`, `52f5b31`, `e9cc8fc`, `ad84232`, `492e260`, `e751a8b`, `652c910`, `fac6c8e`, `655f67d`, `e7a1a30`):** scroll stick-to-bottom (web/src/views/chat.ts:92-94,178-191); nav-interrupt / no-abort-on-unmount (chat.ts:32-42 + store `activeChatStreaming`); clipboard `execCommand` fallback (markdown.ts:87-99); stop button (composer.ts:152, chat.ts:381-389); `model_loading` banner (chat.ts:267-269,161-166); summary banner UI (chat.ts:70-86); `reasoner`→canonical swap-name alias (orchestrator.py:227-250,381); `list_models` live state from `/v1/models` (settings/api.py:111-125); GPU inventory trace spam removed (gpu/api.py:69-73); shutdown drain 8s (background/__init__.py:26,54-63); router kwargs (orchestrator.py:317-325); error-path partial persistence (orchestrator.py:481-503); warmup per-model 60s timeout + logging (warmup.py:59-74).

**Still open (see FIX_PLAN for file:line):** warmup silent no-op risk (main.py:96 unstored task ref, `_warmup_loop` no try/except, `llm_client` only set by background.start); `_on_turn_complete` success-path only (orchestrator.py:456); `reasoning_content` dropped (types.py `ChatDelta` lacks field, llm_client._parse_stream_chunk:255 reads content only); web retry/usage-inline/thinking-display gaps; config drift (CUDA_DEVICE_ORDER missing, settings.local.yaml overlay drift, classifier timeout 6s, legacy ollama tags in settings.json).

**Exploration note (deferred, after all fixes live):** MoE ctx headroom via `--n-cpu-moe` offload for `chat-default` (Qwen3.6-35B-A3B). Precedent: `qwen3-coder-30b` stable with `-ncmoe 20` in llm-stack config (~2.9GB/GPU headroom). See `docs/AGENT_CONTEXT_MEGA.md` §9.

## Where things stand (2026-08-06 — post-feature-implementation live re-test)

Session implemented three user-requested features: regenerate button,
tokens/second display, and swap-aware routing (sticky routing to avoid
unnecessary GPU0 swaps). All features committed (8c4f7b4, 136 tests passing).
User re-tested and reported 5 new issues. **Nothing in this entry is fixed
yet except where noted.**

### 1. Sticky routing works but doesn't visually show "loading model" when swap occurs — FIXED (commit 0170ca4 line of work; chat.ts:267-269,161-166)
Swap-aware routing (prefer currently-loaded GPU0 model when classifier
confidence < 0.8) is implemented and working — confirmed by user. However,
when a swap DOES occur (classifier confidence >= 0.8, or no GPU0 model
currently loaded), the UI doesn't show the "loading model" banner. The
`model_loading` SSE event is sent by the orchestrator
(`orchestrator.py:400`), but the frontend may not be displaying it correctly,
or the swap is happening so fast that the banner flashes and disappears.
**Status: NOT YET INVESTIGATED. Next session: check if `model_loading` events
are being sent and received correctly; verify the banner display logic in
`chat.ts:235-237`.**

### 2. Navigating to Debug page while messaging interrupts it — FIXED (commit 53dac3a)
**Root cause:** Chat view's `unmount()` called `abort?.abort()`, which canceled
the SSE stream. In-app navigation triggers `unmount()` via the router, but
browser tab switching does NOT (only `pagehide`/`beforeunload` on tab close).
**Fix:** Don't abort the SSE stream on unmount. Track streaming state globally
in the store (`activeChatStreaming`) so it persists across view changes. The
stream continues in the background, and when the user returns to Chat, they
load the completed message from DB. The stop button still aborts explicitly.

### 3. Scrolling while generating still broken — FIXED (commit 53dac3a)
**Root cause:** The `wasNearBottom` check was calculated before the DOM update,
but the scroll assignment happened after in a single `requestAnimationFrame`.
If the user scrolled up between the check and the assignment, they were still
yanked back down. Also, the 80px threshold was too generous.
**Fix:** Use double-`requestAnimationFrame` to ensure layout is fully committed,
then re-check the scroll position inside the callback. Reduced threshold from
80px to 20px. Now only auto-scrolls if the user is STILL near the bottom after
the layout settles.

### 4. Can't tell if summarizer is doing anything in Debug mode — FIXED (summary banner UI, chat.ts:70-86)
User reports that the rolling summary feature doesn't show any visible
activity in the Debug panel. The backend generates summaries every 6 turns
(`background/summaries.py`, `config.yaml:233`), and the Debug view should
show a `summary` span when it runs. **Status: NOT YET INVESTIGATED. Next
session: (1) verify that `maybe_enqueue_summary` is being called after every
turn (`orchestrator.py:439-440` calls `_on_turn_complete`); (2) check if the
summary job is actually running (add logging to `summaries.py:110-130`);
(3) verify that the summary span is being emitted and reaches the Debug
SSE stream; (4) check if the summary is being written to `chats.summary`
in SQLite (query the DB directly to confirm).**

### 5. Classifier timeout for trace_id: 31a0033c-59c9-451a-b495-2e24223f2ee9 — ROOT CAUSE FOUND, PARTIALLY FIXED
**Root cause:** Classifier process was cold (not loaded yet) at 03:07:39.
llama-swap logs show:
- 03:07:39: classifier request arrived, proxy tried localhost:5801 → "connection refused"
- 03:07:45: classifier health check passed (process started)
- 03:07:45: request canceled ("context canceled") after 6s timeout
- 03:07:52: classifier finished loading (1m32s cold load!)
The 6s timeout (`config.yaml:226`) is too short for a cold classifier load.
The classifier is CPU-resident but still takes 1m32s to load on first request.

**Why wasn't the classifier warmed on startup?** The warmup loop (`app/main.py:56-66`)
should ping all resident models on startup, but journalctl shows NO warmup logs
after the 02:59:46 restart. The warmup task is created (`asyncio.create_task`)
but either isn't running or is failing silently before logging.

**Partial fix (commit 0170ca4):** Added explicit logging to the warmup loop so we
can see if it's running. Next restart should show "warmup loop: starting sweep"
in journalctl. If it still doesn't appear, the task isn't being created or is
crashing before the first log.

**Remaining issue:** Even with warmup, the 6s classifier timeout is too short
for a cold load (1m32s measured). **Fix options: (1) increase `routing.classifier.timeout_s`
to 90s or more; (2) ensure warmup actually loads the classifier before the first
request; (3) add a "classifier cold" warning in the UI when the first request
takes >10s.**

**User requirement:** "The classifier should always be hot including the 2 models
on CPU. Everything depends on those models so swapping should never kill it.
When the AI process spawns, the hot models should auto-load including chat-default
since that will probably always be the first one used."

This means:
- CPU-resident models (classifier, dispatcher, utility, embed) must be eagerly
  loaded on startup and never swapped out (ttl_s: 0 in config, which is correct)
- chat-default (GPU0) should also be eagerly loaded since it's the default
- The warmup must actually run and succeed for all resident models

### What was fixed in this session
- **Commit 8c4f7b4**: Regenerate button, tokens/second display, swap-aware routing, warmup timeout, real loaded state, residency drift fix, drain timeout reduction
- **Commit 53dac3a**: Scrolling fix (double-rAF + re-check), navigation interrupt fix (global streaming state)
- **Commit 0170ca4**: Warmup logging, dedup logic fix

## Where things stand (2026-08-06 — post-restart-fix live re-test)

Follow-up session after the classifier-timeout/context-truncation fixes
landed (`app/warmup.py`, summary compaction in `orchestrator.py`,
`settings.local.yaml` classifier `timeout_s`/`ttl_s` correction) and a
systemd unit fix (`--timeout-graceful-shutdown 10` added to
`ai-mega-app.service` — `systemctl restart` was hanging indefinitely
because uvicorn's default graceful shutdown waits forever for open SSE
connections, e.g. the Debug panel's `/api/debug/stream`, to close; fixed
live, confirmed working). User re-tested and reported 4 more issues.
**Nothing in this entry is fixed yet except where noted.**

### A. "All models unloaded after chat completion" — turned out to be a UI/API lie, not real unload — FIXED (`list_models` now queries `/v1/models`, settings/api.py:111-125)
`app/settings/api.py::list_models()` (`GET /api/models`, backs the
model-picker roster) **hardcodes `"loaded": False` for every model,
always** — it's a stub predating real llama-swap wiring
(`main.py:112`'s own comment calls it a placeholder pending "Phase 2's
`GET /api/models` adds resident/loaded flags", docs/FEATURES.md F3).
It never queries llama-swap for real state, so the UI *always* shows
every model as unloaded regardless of what's actually resident — this
is why the user saw "no models loaded" right after a restart and
assumed sending a message would fix it. **Real fix:** wire this to
llama-swap's actual running-state endpoint — confirmed live via
`curl http://127.0.0.1:8080/v1/models` that llama-swap already reports
per-model `status.value` (`"loaded"`/`"unloaded"`), and
`curl http://127.0.0.1:8080/running` gives full process detail
(cmd/proxy/ttl) for what's actually up. Either is a clean data source;
`/v1/models` is the smaller shape and probably the better fit for the
roster endpoint.

**However — while checking whether this was "just" a UI bug, found a
second, real problem in the same area, unresolved:** live `curl` right
after a fresh restart showed only `coder` actually running; `chat-default`,
`classifier`, `dispatcher`, `utility`, `embed` — all `resident: true`
and supposedly eagerly warmed by `app/warmup.py`'s startup
`_warmup_loop` — showed `"unloaded"`. `journalctl -u ai-mega-app` had
**zero** `warm-up complete`/`warm-up failed` log lines for any of them
across two full restart cycles (the only warm-up log line in the entire
journal is one stale `warm-up failed (embed)` from *before* this
session's embed-endpoint fix). That means `warmup_one()` isn't even
reaching its own `try`/`except` logging for most models — not failing
loudly, just never completing/logging at all. Prime suspect, not yet
confirmed: `settings.local.yaml` currently marks **`coder`, `coder-small`,
and `vision` as `resident: true`** in addition to the intended
`chat-default`/`dispatcher`/`utility`/`embed`/`classifier` set — that's
three ~24-32GB GPU0 models simultaneously demanding permanent residency
on a single 24GB 3090, which `config.yaml`'s own defaults correctly mark
`resident: false` for (GPU0 is a `swap: true` group by design, see
existing entry #3 below). If `warmup_resident_models`'s
`asyncio.gather` fires pings for all of them concurrently, GPU0 likely
thrashes/deadlocks trying to satisfy three mutually-exclusive residency
demands, which could stall the whole gather (Python's `asyncio.gather`
doesn't fail-fast per-task in a way that would explain zero logs from
the *independent* CPU-resident models unless something upstream — the
`llm.chat()` httpx call itself — hangs without ever raising). **Next
session: (1) revert `coder`/`coder-small`/`vision` to
`resident: false` in `settings.local.yaml` to match `config.yaml`
intent, restart, and check whether `warm-up complete` lines start
appearing for the CPU models; (2) if they still don't, add a per-call
`asyncio.wait_for` timeout around each `warmup_one` ping so one hung
model can't silently swallow the others' completion, and log entry/exit
explicitly (right now there's no "attempting warm-up" log, only
completion/failure, so a hang before either is invisible).**

### B. Browser-tab-switching bug (issue #5 in the 2026-08-05 entry below) — still open, not yet re-investigated this session
User confirms this is still reproducing. No new investigation done this
session beyond re-confirming it's not fixed — see existing entry #5
below for the known architecture gap (aborted SSE stream never persists
partial content).

### C. Scroll bug still reproducing despite the `requestAnimationFrame` fix
The `chat.ts` fix from the prior session (wrapping `sc.scrollTop =
sc.scrollHeight` in `requestAnimationFrame`) did not resolve what the
user is seeing. That fix only addressed a *timing* race (assignment
before layout); it does **not** address issue #8 in the 2026-08-05 entry
below (auto-scroll fighting a manual scroll-up during streaming, because
every token still force-scrolls unconditionally) — that's almost
certainly the actual bug the user is still hitting, since #8 was never
fixed, only the unrelated rAF timing issue was. **Next session: implement
the "only stick to bottom if already within N px of it" fix described in
#8**, not another timing tweak.

### D. Rolling summary never produces a summary message, and a `first_token_timeout` breaks the chat entirely — needs live repro
User's repro: chat titled around "show current system time" hit a
`first_token_timeout` error, and **no summary message appeared after
the error**. Two distinct things tangled together here, neither
confirmed root-caused yet:
- `first_token_timeout` breaking the chat outright suggests the SSE
  error path doesn't leave the conversation in a recoverable state —
  worth checking whether `orchestrator.py`'s error handling for that
  specific `LLMError` kind persists anything to SQLite or just aborts,
  and whether the frontend shows a retry affordance or just silently
  breaks (user says "the chat broken" — get the exact visible symptom:
  stuck spinner? console error? messages gone?).
- Separately, per existing entry #7 below, summaries were already known
  to be **generated server-side but never surfaced in the chat UI at
  all** — so "no summary message after this error" may be expected
  today regardless of the timeout (there was never a UI element for it
  to appear in). Don't conflate "summary didn't visibly appear" (#7,
  known, unfixed) with "summary generation itself failed because of the
  timeout" (unconfirmed) — check `chats.summary` in SQLite directly for
  that chat_id to tell which one actually happened before assuming a new
  bug.

**Priority suggestion for next session:** A (the real-loaded-state wiring
+ the warm-up hang) and D (chat-breaking error) are the sharpest —
A because the `settings.local.yaml` triple-resident drift could be
actively causing GPU contention beyond just cosmetics, D because a
"broken chat" is a hard stop for the user, not a cosmetic bug. C has a
known fix already written up (#8 below), just not applied yet.

## Where things stand (2026-08-05 — live UI bug sweep, NOT YET FIXED)

User ran a live test pass against the running app on `ailab` and reported
10 issues in one batch. This session investigated the code for each one
to classify it before any fixes land — **nothing below is fixed yet**,
this is triage only. Ordered roughly by severity/confidence.

### 1. Classifier fallback: `chat — fallback: timeout` — CONFIRMED, needs live repro
`routing.classifier.timeout_s` is `6.0` (`config.yaml`), `classifier` is
`resident:true` (`ttl: 0` in the generated `llama-swap.yaml`), so it
should already be loaded and answering in ~0.9-1.1s warm per the docs'
own numbers (`app/router/classifier.py` docstring). A hard 6s timeout
firing on a resident model suggests either: the classifier process
crashed/isn't actually up (check `curl :8080/health`, check
`llama-swap`'s own process list, not just the config file), CPU
contention from a concurrent `utility` job (both are CPU-resident and
share the same CPU budget — a summary job mid-flight could starve the
classifier), or general box load. **Next step: reproduce live and check
`journalctl`/llama-swap logs for the classifier process at the moment of
a timeout**, not a code fix — nothing in `classifier.py` looks wrong on
read-through.

### 2. GPU inventory spam in Debug — FIXED (trace spam removed, gpu/api.py:69-73)
`web/src/views/debug.ts:367` polls `/api/gpu/inventory` every 5s
(`gpuTimer = setInterval(refreshTelemetry, 5000)`) while the Debug view
is mounted, and `app/gpu/api.py:71`'s `get_inventory()` calls
`new_trace()` + a `gpu_inventory` span **on every single poll**. Every
5s the Debug view is open, a brand-new trace gets created and pushed to
the top of the traces list — this is why it looks like "a loop... new
entries every few seconds." Fix: telemetry polling shouldn't mint a new
trace per call; either stop tracing routine inventory polls entirely, or
reuse one trace_id for the polling session instead of `new_trace()` each
time.

### 3. "Chat-default reloading" every message — LIKELY NOT A BUG, is `gpu0-main` swap-group behavior
GPU0 runs `chat-default`/`coder`/`coder-small`/`vision` in one
`swap: true` group (`llama-swap.yaml`) because the 3090 can't hold two
~24GB big models at once — **only one of those four can be resident on
GPU0 at a time by design** (`PLAN.md` §4.1, Config B). If the user
alternates between a chat message and a code message, GPU0 swaps
chat-default out and coder in, then back — that's real VRAM-constrained
swapping, not a bug, and "check which model is loaded, skip reload"
isn't possible for that group without more VRAM. **If this is instead
reproducing on *consecutive* plain-chat messages with no coder/vision
message between them**, that would be a real bug — needs a live repro
with the exact message sequence, because nothing in `orchestrator.py`
forces a reload of an already-resident model.

### 4. Copy-to-clipboard button visible but doesn't copy — FIXED (clipboard `execCommand` fallback, markdown.ts:87-99)
`navigator.clipboard.writeText()` (`web/src/markdown.ts`, added this
session) requires a **secure context** — HTTPS or `localhost`. The app
is served over plain HTTP at `http://192.168.0.89:8000` (LAN IP, not
`localhost`), so `navigator.clipboard` is `undefined` there and the
button's click handler throws before `writeText` ever resolves/rejects,
silently doing nothing. **This needs a fallback path** (e.g.
`document.execCommand("copy")` via a hidden textarea, or catching the
`undefined` case explicitly and showing "unsupported" instead of hanging
silently). Also "should be instant" — current 1.5s "Copied!" revert
delay is probably fine, but the real complaint is likely just "nothing
visibly happens," which is the secure-context failure, not the delay.

### 5. Navigating away mid-stream and back loses the response — FIXED (error-path partial persistence, orchestrator.py:481-503)
`chat.ts`'s `unmount()` calls `abort?.abort()`, killing the in-flight SSE
connection. The assistant's streamed content only gets persisted to
SQLite in `orchestrator.py`'s `db: persist_assistant_message` span,
which runs **after** the stream completes normally — an aborted stream
never reaches it. So navigating away mid-generation and back reloads
history from the DB, which never got the partial (or even complete, if
the abort raced the final chunk) answer. This is an architecture gap,
not a one-line fix: either persist partial content on abort, or make
generation resumable/backgrounded independent of the view's lifecycle
(the latter matches how the title/summary background jobs already work
— worth reusing that pattern).

### 6. `reasoner`/`reasoner-alt` → 404 "no router for requested model" — FIXED (`reasoner`→canonical swap-name alias, orchestrator.py:227-250,381)
`PLAN.md` §4.1 / `swapgen.py`'s own docstring say `reasoner` is
"*same blob as chat-default*... enabled at the request layer, not a
separate swap entry — routing chat→reasoner costs zero load time."
`swapgen.py::_select_entries` correctly dedupes `reasoner` out of the
generated `llama-swap.yaml` (verified live — `reasoner` has no entry in
`/home/john/llm-stack/serving/llama-swap/config.yaml`). **But nothing
resolves the request-time model name.** `orchestrator.py:213`
(`resolved_model = result.model`) passes the literal string `"reasoner"`
straight to `llm_client.chat(model=resolved_model, ...)`
(`orchestrator.py:241`), and that goes straight to llama-swap, which has
no `reasoner` key → 404. The `thinking=model_entry.thinking` flag *is*
correctly threaded through separately, but the model name itself is
never translated to `chat-default`. **This is the actual fix needed:**
when `resolved_model`'s `ModelEntry.resident` collapsed it into another
canonical entry during swapgen, the orchestrator (or router) needs to
send the *canonical swap-slot name* to llama-swap while still using the
original entry's `thinking`/`max_tokens`/etc. Same root cause explains
`reasoner-alt`, though that one is additionally `enabled: false` in
`config.yaml` — it wouldn't work even with the alias fix until re-enabled.

### 7. Rolling summaries "don't seem to happen" — IMPLEMENTED SERVER-SIDE, NEVER SURFACED IN UI
`app/background/summaries.py` is real and wired: `maybe_enqueue_summary`
fires every `cfg.background.summary_every_n_turns` user turns, writes to
`chats.summary` in SQLite. **Confirmed: zero references to it anywhere
in `web/src/**`** — nothing fetches or displays `chats.summary`. This is
exactly the "built but not injected" failure mode `CLAUDE.md`/`PLAN.md`
call out as a rejected-PR condition — the backend half shipped without
the frontend half. Needs: an API surface for the summary (if `/api/chats`
doesn't already return it — check) and a UI spot to show it (Debug span
already exists at `summary` stage, so it's visible *there*, just not in
the chat view itself).

### 8. Auto-scroll fights manual scroll-up during streaming — CONFIRMED BUG
`chat.ts` calls `renderMessages(true)` on nearly every SSE token during
streaming (lines ~197-227), and `scroll=true` unconditionally does
`sc.scrollTop = sc.scrollHeight` every time. So scrolling up mid-stream
gets yanked back to the bottom on the very next token, which reads as
"can't scroll down" (you're fighting a forced re-snap every ~50-100ms
during generation). Standard fix: only force-scroll if the user was
already within some threshold (e.g. `scrollHeight - scrollTop -
clientHeight < 80`) of the bottom *before* the update — i.e. "stick to
bottom" only while already at the bottom, never yank someone back who
scrolled away on purpose.

### 9. No stop/interrupt button for in-flight generation — FIXED (stop button wired, composer.ts:152, chat.ts:381-389)
`chat.ts` already has an `AbortController` (`abort`) wired to cancel the
SSE stream on unmount, but there's no UI control that calls
`abort.abort()` while staying on the same view — `composer.ts`'s layout
only has a `send-btn`, no stop/cancel button. The plumbing (abort
controller, SSE cancellation) already exists; this is a real UI gap, not
a backend one. Wiring a stop button through the existing `abort` would
also need to (a) not lose the partial content already streamed (see #5 —
same underlying persist-on-abort gap) and (b) flip the composer back to
its idle state.

### 10. Model appears to not see full session history — LIKELY MODEL BEHAVIOR, PLUMBING VERIFIED CORRECT
`app/chat/history.py::build_llm_messages` returns **every** prior
message in the chat as OpenAI-format turns, called fresh before every
`llm_client.chat()` call (`orchestrator.py:239`), and the span already
records `message_count`/`messages` for the Debug view to show — this
matches what the user says they saw in Debug. The wiring looks correct
on read-through. Two real considerations, neither of which is a "send
fewer messages" bug: (a) **no system prompt is ever prepended** —
`build_llm_messages` only returns bare user/assistant turns, no framing
that tells the model it's a persistent app receiving full history each
call, which is exactly the kind of ambiguity a raw local model can
misinterpret and answer generically about "session storage." (b) small/
local models (this roster tops out at 35B-A3B) are meaningfully worse at
this kind of self-referential meta-question than frontier hosted models
even with correct context — this may just be a model-quality ceiling, not
a bug. **Recommend verifying via the Debug prompt tab on the exact turn
that produced this answer** (need `debug.store_prompts: true` per the
span's own doc comment) before assuming there's a plumbing bug — the
code path itself looks right.

### Also flagged, not yet triaged in depth (verify against `PLAN.md`/`docs/FEATURES.md` before treating as bugs)
- **Debug doesn't show if a model is ejected/evicted from a swap slot.**
  No code found that surfaces llama-swap eviction events at all — likely
  not yet implemented (`docs/FEATURES.md` doesn't call out an eviction
  event either), flag as a feature request, not a regression.
- **Tokens/sec + response time next to the model name in chat (LM
  Studio-style).** `PLAN.md` §4.16 says the Debug window is supposed to
  show real tok/s from llama.cpp's `timings`, and `debug.ts:253` already
  renders `predicted_per_second` **inside the Debug span detail view** —
  so the data is captured and shown *somewhere*, just not inline in the
  chat bubble next to the model name as requested. That inline placement
  is a real, reasonable feature request, not a bug — needs `msg.usage`/
  `msg.timings` threaded from the `done` payload into `ChatMsg` and
  rendered in the `msg-meta` row (`chat.ts` ~line 83-104 already has the
  right spot for it, just needs the fields).
- **Debug doesn't show model "thinking" in the stream.** Confirmed at the
  type level: `app/types.py::ChatDelta` has no `reasoning_content` field
  at all, and nothing in `llm_client.py` reads `reasoning_content` off
  llama.cpp's stream deltas. `PLAN.md` §4.2 says thinking tokens "arrive
  inside `token` and are tagged in the `done` payload" — that description
  doesn't match what's actually implemented; reasoning content isn't
  captured anywhere server-side yet. This is a real gap against the
  written contract, not a UI-only fix — needs `ChatDelta.reasoning_content`,
  threading through `llm_client.chat()`, and a Debug-view field to render
  it, in that order.

### Not yet verified live (deploy status unclear)
- **The 2026-08-02 background-shutdown-hang fix (`_STOP_DRAIN_TIMEOUT_S`,
  bug #4 in the entry below) — user reports the hang is STILL happening.**
  The service was restarted 2026-08-05 per this session's earlier work, so
  the fix should be live, but if the hang is still reproducing, don't
  assume the original diagnosis (uncapped background-job drain) was
  complete — re-check `app/background/__init__.py::stop()` end-to-end
  against a live repro rather than assuming the prior fix covers it. Get
  the exact symptom (how long, what's it doing, does it eventually
  recover or need a kill -9) before re-diagnosing.

**Nothing in this entry has been fixed yet — this is triage only, next
session should work through these roughly in the order listed (6 and 4
are the sharpest, highest-confidence root causes; 1 and 3 need live
repro before code changes; the "also flagged" section is scoping work,
not urgent bugs).**

## Where things stood (2026-08-02, later same day — CRITICAL ROUTER BUG) — FIXED (router kwargs, orchestrator.py:317-325)

**The Phase-2 router classifier has never actually run on live chat
traffic, since it was first wired.** `app/chat/orchestrator.py` called
`app.router.route()` as `_route(chat_row, text, attachments)` — no
`llm_client`, `config`, or `trace_id` kwargs. `route()`'s signature is
`route(chat, text, attachments, *, llm_client=None, config=None,
trace_id=None)`, so `llm_client` silently defaulted to `None` on every
real turn, and Layer 3 (the classifier) hit:
```
if llm_client is None:
    logger.warning("router: no llm_client for classifier, using fallback")
    return _fallback_result(fallback_model, elapsed(), "error")
```
instantly, every time — `source="classifier"`, `intent="chat"`,
`confidence=None`, ~0.1ms latency. **Every message that didn't match
Layer 1 (override) or Layer 2 (2+-word keyword rules) silently fell back
to `chat-default`**, no matter what it asked for. This is why "code me a
script" never routed to `coder`. `scripts/eval_router.py`'s 93.33% pass
(logged in the entry below) never caught this because it calls the
classifier directly, bypassing the orchestrator entirely — **that eval
number was real but was never proof the live app used it.**

Fixed in `app/chat/orchestrator.py`: `_route(...)` now passes
`llm_client=self.llm_client, config=self.config` (not `trace_id` — the
orchestrator already wraps the call in its own `span(trace_id, "route",
...)`, so passing it too would double-emit a span for the same stage).

**Why tests didn't catch it:** `tests/test_settings_api.py::
test_orchestrator_uses_router` monkeypatched `_route` with a fake whose
signature was `async def fake_route(chat, text, attachments) -> RouteResult`
— no `**kwargs`, no `llm_client`/`config` params. That fake *worked*
specifically because the orchestrator never passed those args — the test
would have hard-failed with a `TypeError` the moment the real call site
tried to pass them, which is exactly what happened when this got fixed
(it briefly broke, was corrected to accept the real kwargs). **Lesson:
a test double with a narrower signature than the real callee can mask a
missing-argument bug indefinitely — stubs should accept `**kwargs` or
mirror the real signature exactly, not just "whatever the code happens to
call it with today."** Added `test_no_override_forwards_llm_client_and_config_to_router`
in `tests/test_chat_sse.py` as an explicit regression test for this exact
failure mode.

**Second bug found while chasing a live 404 ("no router for requested
model"):** `app/gpu/swapgen.py::_select_entries` dedupes models sharing a
GGUF file (`chat-default` / `reasoner` share one blob) with documented
priority "resident:true over non-resident; ties broken by first in list."
The old code was `elif m.resident: file_to_canonical[m.file] = m.name` —
which re-fires for *every* later resident entry, so if both `chat-default`
and `reasoner` end up `resident:true` (as happened live, via
`settings.local.yaml` drift), `reasoner` — being later in `config.models`
— silently clobbers `chat-default` as the swap-slot survivor and
`chat-default` vanishes from `llama-swap.yaml` entirely. Fixed by
tracking whether the current canonical is already resident and only
letting a later entry displace it if it isn't. Regression test:
`test_both_resident_ties_keep_first_in_list` in `tests/test_swapgen.py`.

**Note (2026-08-05): this fixed a *different* 404 than issue #6 above.**
That earlier bug was about `settings.local.yaml` drift causing
`chat-default` itself to disappear from the swap config. Issue #6 above
is a separate, still-unfixed bug: `reasoner`/`reasoner-alt` are *supposed*
to be deduped away (that's correct, by design) but nothing translates
the request-time model name to the surviving canonical entry.

**Third: `PUT /api/settings/routing` 422'd on `classifier`.**
`RoutingConfig` (`app/config.py`) has `rules`, `attachments`, `intents`,
**and** `classifier`, but `RoutingPatch` (`app/settings/api.py`) only
declared `rules`/`intents` with `extra: forbid` — any Settings-UI save
that included classifier fields (model/timeout_s/confidence_threshold/
fallback_model) 422'd. Added `classifier: dict[str, Any] | None` to
`RoutingPatch` and a matching deep-merge branch in
`app/settings/store.py::update_routing` (mirrors the existing `intents`
handling).

**Fourth: `systemctl restart ai-mega-app` can appear to hang ~15-40s.** — FIXED (shutdown drain 8s, background/__init__.py:26,54-63)
Not a deadlock — `app/background/__init__.py::stop()` deliberately awaits
any in-flight background job before letting the app exit (title jobs are
fast via `dispatcher`, but a summary job on CPU `utility` is documented at
17.6-40s, `PLAN.md` §5). No timeout existed, so a restart right after a
chat turn (especially the 6th, which triggers a summary) could silently
block for tens of seconds with systemd's default 90s `TimeoutStopSec` as
the only backstop. Added a 15s bounded wait
(`_STOP_DRAIN_TIMEOUT_S`) with `asyncio.wait_for` + a warning log +
`BackgroundQueue.cancel()` fallback, so shutdown degrades visibly instead
of silently stalling. **User reports this is STILL reproducing as of
2026-08-05 — see "Not yet verified live" above, re-open this.**

**Fifth (not a bug, but confusing UX, worth documenting):**
`RoutingRule.keywords` rejects single-word entries by design (`app/config.py`
`_each_keyword_is_multi_word` validator) — word-boundary keyword rules are
deliberately restricted to 2+ word phrases to avoid false-positive
substring matches (`PLAN.md:228`, frozen decision). A user trying to add
`"file"` as a rule keyword gets a `ConfigError` — expected; use `"add
file"`/`"open file"` etc. instead.

**Sixth: live overlay drift that triggered bug #2 above.**
`routing.intents.tool_call_needed` had been set to `utility` via the
Settings UI at some point this session — wrong choice (`utility` is
CPU-bound, budgeted for background jobs at 17-40s/call; using it for a
live tool-calling turn would make every such message feel broken-slow).
Reverted to `chat-default` on the live overlay. `reasoner.resident` was
also `true` on the overlay (should be `false` — it shares chat-default's
blob and doesn't need its own residency); reverted.

**Status at time of writing:** all 6 fixes above are code-complete,
106 tests passing, `tsc --noEmit` clean. **`app/background/__init__.py`
and `app/background/queue.py` (fix #4) have NOT been deployed yet** — the
last `systemctl restart` predates that edit. A restart is needed before
the shutdown-timeout fix is live. The orchestrator/swapgen/settings fixes
(#1-3) were deployed and live-verified via curl before this note was
written.

**The user is planning to have a more powerful model do a deeper audit
of the router before further fixes** — given how long bug #1 went
undetected (the classifier layer effectively never ran since Phase 2
merged), treat the router/orchestrator seam as under-tested and don't
assume anything else in that path is correct without checking.

## Where things stood (2026-08-02, earlier)

**Phase 2 exit criteria are now both confirmed on live hardware (ailab):**
- `scripts/eval_router.py --base-url http://127.0.0.1:8080/v1 --min-accuracy 90`
  → **93.33% (126/135)**, passes the ≥90% gate. (Note: the `--base-url`
  must include `/v1` — `config.yaml`'s `llama_swap.base_url` does too —
  passing the bare host:port 404s every classifier call and silently
  routes everything to `chat`, which looks like a router bug but isn't.)
- GPU-reassignment demo: `PUT /api/settings/models/coder-small {"gpu":1}`
  → `POST /api/gpu/apply` → verified `/home/john/llm-stack/serving/llama-swap/config.yaml`
  updated (`CUDA_VISIBLE_DEVICES=1`) and `ai-mega-app.service`
  `InvocationID` unchanged (no restart) in ~34ms. Reverted back to gpu:0
  after.

**Test-isolation gap fixed (`e7a1a30`):** `tests/test_config.py`'s two
`ModelEntry` new-field tests didn't monkeypatch `OVERLAY_PATH` like the
rest of the suite does, so they silently depended on no
`settings.local.yaml` existing at repo root. Once the user actually used
the Settings UI (writing a real overlay), those two tests started failing
against real model paths/`resident` values instead of their own fixture
data. Fixed to match the existing pattern (see `test_settings_api.py`
`_isolate` fixture for the canonical version). **General lesson:** any
new test calling `load_config()`/`get_config()` with a custom path must
also monkeypatch `config_mod.OVERLAY_PATH` (and `store_mod.OVERLAY_PATH`
if touching the settings store) or it's implicitly coupled to whatever
happens to be in the real overlay file that day.

Full verification gate (`pytest`, `tsc --noEmit`, real `tsc` rebuild) is
green; `web/js/**` matches `web/src/**` exactly (only the settings_models
GPU-name-in-label diff, now committed as `e7a1a30`).

## Where things stood (2026-07-31)

All 7 Phase 2 branches are merged to `main` (`config-schema`,
`router-classifier`, `gpu-swapgen`, `background-utility`, `router-eval`,
`settings-api`, `settings-ui`). This was the user's **first live test**
of the app end-to-end (previously all fake-data/unit-test verified).
Two real bugs surfaced during that live test and are now fixed and
pushed (`fac6c8e`, `655f67d`). `docs/PHASE_PROMPTS.md` Phase 2 exit
criteria not yet run on live hardware: the GPU-reassignment reload demo
and `scripts/eval_router.py --min-accuracy 90` against the real
classifier. Don't assume those passed — nobody has run them yet.

`package-lock.json` is untracked at repo root (shows in `git status`).
Nobody has decided whether it should be committed or gitignored — ask
before doing either.

## Deployment model — read this before debugging "it's not updating"

- The FastAPI backend on `ailab` (`app/main.py`) mounts `web/` via
  `StaticFiles(..., html=True)` and serves files **straight off disk**.
  There is no build/bundle/deploy step for the frontend at request time.
- This means: editing `web/src/**` requires running `npx tsc` (real
  build, not just `--noEmit`) and the output lands in `web/js/**`,
  which **is checked into git** (not gitignored) and must be committed
  alongside the `.ts` source. A merge or edit that touches `web/src/**`
  without rebuilding `web/js/**` ships stale JS silently — this bit the
  `p2/settings-ui` merge earlier this session.
- Once `web/js/**` and `web/css/**` are correct on disk, they are
  **immediately live** — no `systemctl restart ai-mega-app` needed,
  just a browser hard-refresh (Ctrl+Shift+R) to bust cache. Only
  `app/**` (Python) changes require a service restart.
- The Windows laptop is a pure browser client hitting
  `http://192.168.0.89:8000` — nothing to `git pull` there, ever.
  **This also means `navigator.clipboard` (secure-context-only) fails
  there — see issue #4 above, this bit the copy-button feature the same
  day it shipped.**

## Two real bugs found + fixed this session (patterns worth remembering)

1. **`fac6c8e` — Settings view laid out as a vertical stack instead of
   rail+panel side-by-side.** Root cause: `settings.ts` does
   `el.className = "settings-view"` on the *same element* that has
   `id="view"`. `app.css` pins `#view { flex-direction: column }` by
   ID. An ID selector always beats a class selector regardless of
   source order or later declarations — so `.settings-view`'s own
   `flex-direction` never won, no matter what I set it to, until I
   scoped the override as `#view.settings-view` (ID+class beats bare
   ID). **General lesson: if a view reuses a shared host element by ID
   and re-styles it via a class, any property the ID rule also sets
   needs an `#id.class` override, not a bare `.class` one.**

2. **`655f67d` — Debug view showed the raw `chat_id` UUID as the trace
   row's "name", and defaulted to the wrong trace on open.** The title-
   generation background job (`app/background/titles.py`) calls
   `new_trace(chat_id)` — a **separate trace_id** from the actual chat
   turn's trace, sharing only `chat_id`, with exactly one span
   (`stage="title"`, `model=<title_model, usually "dispatcher">`).
   Traces are listed most-recent-first, and the title job runs *after*
   the turn it's titling, so it was almost always `traces[0]` — meaning
   opening Debug right after a chat showed a lone "title" span instead
   of the real turn. Fixed by resolving `chat_id` against the store's
   already-fetched `chats` list for display, and by auto-selecting the
   first trace that isn't title-only. **General lesson: any background
   job that calls `new_trace()` on the same `chat_id` as a live turn
   will outrank that turn in "most recent" ordering — the debug UI (or
   future features) needs to account for that whenever it assumes
   `traces[0]` means "the turn I just ran."**

## 2026-08-15 — llama-server multi-slot KV-cache loss + summary redundancy/quality

- **`-np`/`--parallel` defaulted to `-1` (auto) and resolved to 4 slots for
  `coder` live** (confirmed via `GET /slots`). With no session pinning from
  our client, a multi-turn chat's requests round-robin across slots and
  almost never land on the same one twice, so the KV-cache prefix is lost
  every turn even on a pure append. Traces `63825427`/`c7763bd7` showed
  `cache_n=0` and 10-14s of full prompt reprocessing on consecutive turns,
  misread in the debug panel as a model swap (the `model_loading` heuristic
  fires on any first-token gap > `FIRST_TOKEN_WARN_S`, not just real swaps).
  **Fix: `--parallel 1` pinned globally in the llama-swap macro**
  (`app/gpu/swapgen.py`) — every model gets exactly one slot, guaranteeing
  cache reuse across turns, and incidentally reclaims the 3x context memory
  auto-mode had reserved per model. Needs a service restart (code change,
  not config) + `/api/gpu/apply` to land.
- **Rolling summary redundancy + quality, rewrote `app/background/summaries.py`:**
  `_apply_summary_compaction` always kept the last `summary_every_n_turns`
  raw turns in the prompt, but `_run_summary` regenerated from the *entire*
  transcript every time — at the moment of regen the summary and the raw
  tail covered the exact same turns, paying for that overlap twice, and a
  small CPU model (`utility`, qwen3-8b) asked to re-compress an ever-larger
  transcript into the same 1024-token budget every time trended toward
  generic filler ("user expressed frustration... assistant apologized...").
  Redesigned to match how Claude Code's own `/compact` works: trigger off
  real context pressure, not a fixed cadence, and fold in only the delta —
  - **Trigger**: real `usage.prompt_tokens` from the chat's most recent
    `llm_stream` span (never a client-side estimate, PLAN.md §4.16) crossing
    `background.summary_token_threshold` (default 4000). Falls back to the
    old `summary_every_n_turns` cadence only when no usage data exists yet
    for the chat (first turn, or a test harness seeding messages directly).
  - **Delta**: only messages since the previous `summary` span's timestamp
    get sent to the summarizer, folded into the prior summary — not the
    whole transcript again. No new DB column: the timestamp comes from the
    existing `traces`/`spans` tables (`summary` stage already carries
    `chat_id`). Second-resolution `messages.created_at` vs ms-resolution
    `spans.started_at` means same-wall-second messages are always treated
    as "new" (inclusive floor) — occasional harmless re-inclusion beats
    silently dropping a genuinely new message.
  - **Prompt**: structured sections (key facts, decisions, user
    preferences, open questions) instead of one free-form paragraph —
    higher signal density at the same token budget.
  - In-flight guard (`_in_flight` set) prevents duplicate concurrent
    summary jobs for the same chat while a regen is running.
  - **Trigger is ctx-relative, not a flat number**: threshold is
    `summary_context_fraction` (0.5) of *the turn's own model's* ctx
    (looked up from the `llm_stream` span's `model` field against
    `cfg.models`), since the roster spans ctx 8192 (coder-small/vision) to
    32768 (chat-default/reasoner) — a flat token count would be too eager
    for the small end or too late for the large end. Falls back to the
    flat `summary_token_threshold` (4000) only if that turn's model can't
    be resolved against the current roster.
  - **`_apply_summary_compaction`'s tail-vs-summary overlap in the live
    chat prompt is now also closed** (`app/chat/orchestrator.py`): the
    compacted tail excludes everything at or before the last `summary`
    span's timestamp instead of always keeping a fixed
    `summary_every_n_turns`-sized tail regardless of what the summary just
    covered. Same zero-schema approach (query traces/spans by chat_id +
    stage), same inclusive same-second floor for the second-vs-ms
    resolution mismatch. Falls back to the old fixed-tail behavior when no
    matching `summary` span exists (e.g. `chats.summary` set out of band
    in a test) — still correct, just without the trim.

## 2026-08-15 — classifier thread cap regressed routing latency 7x

Live trace `91017f4d` (chat `c33ddccd`) showed `route` (classifier layer)
taking 7633ms — and it wasn't a one-off: three consecutive turns in the
same chat all landed at 6.9-7.6s. Confirmed live with a direct curl to the
classifier port: 2.6s for a 15-token prompt / 20-token completion, no other
CPU-resident model running concurrently (load average 1.0, no summary/embed
span active in that window) — a genuine steady-state regression, not
contention or a cold start (`journalctl`'s classifier health-check lines
line up with normal per-request checks, not a restart).

Root cause: the `--threads 4` cap applied to `classifier` in the earlier
"CPU thread contention" fix (`e13718e`, same session as `utility`'s cap)
had no dedicated justification — it was applied symmetrically alongside
`utility` and `embed` without evidence classifier itself was the
contention source. `utility`'s cap was justified: a summary run is
long-lived (1024 max_tokens, 17-40s) and was observed starving concurrent
GPU-side work. `classifier` is the opposite shape: it runs on the hot path
for **every** chat turn's routing (not just during a rare concurrent
summary), and each call is brief (max_tokens=64, ~1s even uncapped) — so
capping it traded a rare, short contention risk for a 7x tax on every
single turn. **Fix: raised classifier's `--threads` from 4 to 16** in both
`config.yaml` and `settings.local.yaml` (the overlay had the same stale
value and would have silently reasserted the regression). `utility` and
`embed` caps are unchanged — no evidence either needs revisiting.

## 2026-08-15 — classifier requantized Q8_0 → Q4_K_M

`--threads 16` fixed the CPU-contention regression but the classifier still
floored at ~14.5 tok/s CPU decode even fully warm — `Qwen3-1.7B-Q8_0.gguf`
is ~2x heavier per token than a Q4 quant. Downloaded
`Qwen3-1.7B-Q4_K_M.gguf` from `unsloth/Qwen3-1.7B-GGUF` (same repo family
`download_models.sh` already uses for the big models; Qwen's own official
GGUF repo only carries Q8_0, no Q4 variant). Re-ran
`scripts/eval_router.py --base-url http://127.0.0.1:8080/v1 --min-accuracy 90`
against the live swap: **92.59% (125/135)**, above the ≥90% gate and
slightly above the old Q8_0 baseline (91.76%) — no accuracy regression.
Live decode confirmed ~24.7 tok/s post-switch (vs ~14.5 tok/s), classifier
calls now ~0.6-0.7s warm. `Qwen3-1.7B-Q8_0.gguf` is left on disk
(`/home/john/llm-stack/models/gguf/`) for instant revert (swap `config.yaml`
+ `settings.local.yaml`'s classifier `file`/`quant` back and re-apply) if
this quant ever regresses in practice.

## 2026-08-15 — Debug view: total chat context usage chip

Added a chip next to the route chip in the waterfall header
(`web/src/views/debug.ts`'s `contextUsageHtml`, styled in `web/css/debug.css`)
showing `<used>/<ctx> tok (<pct>%)` for the selected trace — real
`prompt_tokens` from the `llm_stream` span's `usage` field (llama.cpp's own
count) against that turn's model's ctx (fetched once via `listModels()`,
keyed by alias). Turns amber at 50% usage, red at 90% — the same 50%
threshold `app/background/summaries.py`'s trigger watches, so this chip
doubles as "how close is this chat to summarizing." Falls back to a plain
`<used> tok` chip if the model's ctx can't be resolved (roster fetch
failed, or the span predates a roster entry). No server changes — this is
purely reading data the backend already records.

## 2026-08-15 — summarizer trigger: floor against the smallest routable ctx too

User caught this from the new Debug context chip: two consecutive turns in
the same chat showed different ctx denominators (32768 then 16384) because
the router sent them to different models (`chat-default` then `coder`) --
correct, since routing is per-turn. But that surfaced a real gap in the
summarizer trigger: `maybe_enqueue_summary` only checked usage against
*that turn's own model's* ctx. A chat sitting calmly at, say, 7% of
chat-default's roomy 32768 ctx could get routed to `coder-small` (ctx 8192,
or smaller still) the moment a message reads as a coding question, and
that turn would blow straight past the small model's real budget with zero
summarization warning -- landing in `_truncate_to_ctx`'s hard,
unsummarized truncation instead of a graceful compaction.

Fixed in `app/background/summaries.py`: added `_min_routable_ctx(cfg)`,
which resolves every model named in `routing.intents` + the classifier's
`fallback_model` against the roster and returns the smallest ctx among
them -- the tightest budget the router could plausibly send the *next*
turn to. `maybe_enqueue_summary` now thresholds against
`min(this_turn's_model_ctx, min_routable_ctx) * summary_context_fraction`,
so growth is judged against whichever is smaller: what's actually been
used, or what could be used next. New test:
`test_summary_threshold_uses_smallest_routable_ctx_as_floor`.

## 2026-08-15 — summarizer overflowed its own ctx; cursor redesigned to an exact count

User caught two failed `summary` spans back-to-back (traces `fe85c4f7`/
`85b0fbc8`) both erroring `request (8616 tokens) exceeds the available
context size (8192 tokens)`. Not a duplicate-trigger bug -- that's
`BackgroundQueue`'s documented retry-once-on-failure firing a second,
identical, immediately-failing attempt. The real bug: this chat's
first-ever regen tried to summarize its entire 20-message backlog in one
shot, and the formatted transcript (~8600 tokens) alone exceeded `utility`
model's own ctx (8192) -- smaller than the chat model (`chat-default`,
32768) being summarized. The request always 400'd, `chats.summary` never
wrote, so every later turn kept re-triggering the same doomed oversized
regen forever.

Fix in `app/background/summaries.py`: `_fit_to_budget` now caps the delta
sent to the summarizer at that model's own ctx budget, keeping the
*oldest* prefix that fits and leaving the remainder for the next regen --
a long backlog gets compacted in successive ctx-sized bites instead of
failing outright.

That in turn broke the "what's already summarized" cutoff, which used to
be the summary span's own wall-clock `started_at` timestamp
(`app/chat/orchestrator.py`'s `_apply_summary_compaction` and
`app/background/summaries.py`'s delta filter both read it). A partial
regen still stamps "ran just now," so a timestamp cutoff can't
distinguish "covered by this regen" from "just happened to arrive after
it" -- the still-uncovered newer half of an oversized delta would get
silently treated as already-summarized and dropped from every future
prompt. Replaced with `last_summary_covered_count`: an exact message count
(in `history.list_messages`'s stable order) stored as a
`covered_message_count` field inside the summary span's existing free-form
data JSON -- still no new DB column, just no longer ambiguous. Both
`_run_summary`'s delta computation and orchestrator's compaction tail now
slice by this exact position instead of filtering by timestamp.

New test: `test_summary_delta_exceeding_summarizer_ctx_splits_across_regens`
reproduces the live scenario end-to-end (oversized first regen → partial
summary → second regen picks up exactly the leftover, verified present in
that call's actual prompt).

## Tooling notes

- **Playwright is installed as a devDependency but Chromium can't
  launch on this box** — `chrome-headless-shell` needs `libasound.so.2`
  which isn't installed, and installing it needs
  `sudo npx playwright install-deps chromium` (the user approved this
  once this session but sudo needed an interactive terminal and never
  actually completed — this is still not installed). **Firefox works
  out of the box with no missing libs** (`npx playwright install
  firefox` once, then `const { firefox } = require('@playwright/test')`)
  — use Firefox for any future visual verification on this box unless
  someone installs the Chromium system deps.
- To actually verify a frontend fix instead of guessing from reading
  CSS: launch Firefox via Playwright against `http://127.0.0.1:8000`
  (this box is `ailab`, so localhost is the live app), screenshot, and/or
  `page.evaluate(() => el.getBoundingClientRect())` /
  `getComputedStyle(el)` to get real computed layout — this is how both
  bugs above were actually root-caused, not guessed at from screenshots
  alone. Guessing from a screenshot without checking the DOM/CSS wasted
  significant time before this was tried.
- `#/debug` opens a long-lived SSE connection
  (`streamDebugSpans`/`/api/debug/stream`), so
  `page.goto(..., { waitUntil: "networkidle" })` **times out** — use
  `waitUntil: "load"` plus a fixed `waitForTimeout` instead.

## 2026-08-24 — Pi tool-loop throughput and explicit reasoning aliases

The owner tested the actual `earendil-works/pi` coding loop against Qwen3.8-27B through a temporary capture
relay. The original 12–14 tok/s observation was not a context-window problem: representative requests had
7,376–8,932 prompt tokens, with 7,041–8,562 already cached. Pi did send four tool schemas each turn and retained
assistant `reasoning_content`, but neither a relay nor removing `ngram-mod` speculation accounted for the slow
end-to-end runs.

The useful A/B is server-side reasoning. On a matched Pi task, reasoning enabled made 10 calls, emitted 4,267
completion tokens, and took 205.5s. With the chat alias launched using llama-server reasoning off, it made 7
calls, emitted 1,462 completion tokens, and took 63.8s: **3.2x faster**. Keep reasoning off for the normal
interactive/coding-agent alias; route deliberate difficult work to an explicit thinking alias instead. Do not
try to suppress thinking with a `/no_think` prompt suffix: use llama-server flags.

`settings.local.yaml` now expresses that role split: `chat-default` is Qwen3.8-27B at 131,072 context with
reasoning off; `reasoner` points at the same GGUF through the symlink
`/home/john/llm-stack/models/gguf/Qwen3.8-27B-UD-Q4_K_XL-reasoner.gguf` and enables medium reasoning plus a
5,000-token reasoning budget; `reasoner-alt` remains DeepSeek-R1 32B. The symlink is intentional: llama-swap
deduplicates identical file/GPU aliases, so the thinking Qwen entry needs a distinct canonical path. Applied
configuration passed `scripts/config_drift_check.py` before the comparison.

For a bounded live quality comparison, `scripts/eval_quality_transcripts.py` now accepts an optional `--model`
alias and was run serially through llama-swap with the five objective prompts r1/r2/r5/r6/r7 and `--n-predict
5120`. Both aliases answered all five correctly. Final non-duplicate Qwen runs: 111.3s and 3,081 completion
tokens (~27.7 end-to-end completion tok/s). DeepSeek: 141.3s and 3,243 (~23.0). This is not a general benchmark:
the response lengths differ, `r3` was excluded because its fixture answer is mathematically wrong, and r4 is
open-ended. Raw records: `logs/benchmarks/quality/reasoner.jsonl`; the earlier accidental overlapping Qwen retry
left duplicate `r5` entries, which must not be counted as independent samples.

Verification after the evaluator change: `npx tsc --noEmit`, Python byte-compilation, and `git diff --check`
passed. The full pytest suite was attempted with `.venv/bin/python -m pytest -q --basetemp=.pytest-tmp/run` but
produced no output for roughly 150 seconds and was stopped cleanly; do not report it as passing without resolving
that existing suite stall.

## 2026-08-24 — Pi relay diagnosis and web-search extension slowdown

This is the latest continuation point for a new agent session. The owner is running `earendil-works/pi` on a
laptop against the local llama-swap endpoint on `ailab`. A temporary transparent capture relay is running on
the GPU box:

```text
/tmp/pi_request_capture_proxy.py
listen: 0.0.0.0:8081
upstream: 127.0.0.1:8080
allowed client: 192.168.0.246
captures: /tmp/pi-request-captures/*.json
process session: 30385
```

Pi's endpoint is `http://ailab:8081/v1`. The relay initially buffered chunked SSE because it used
`response.read(65536)`, making Pi appear to generate nothing. It was fixed to use `response.read1(8192)` and
the current relay streams correctly. Keep the relay temporary and stop/remove it after live tracing is complete;
do not delete captures without confirming the owner no longer needs them because they contain prompt content.

### Confirmed throughput findings

The 12–14 tok/s observation is not explained by Pi sending the whole context without caching. Recent normal Pi
requests had roughly 12.1–13.3k prompt tokens, with about 12.1–13.2k cached. A plain no-tool Qwen request
completed at about 34.6 output tok/s. Tool-loop requests still decode around 8–10 tok/s and have a roughly
14.9k-character system/tool scaffold.

Normal Pi turns register 28 tools: base file/shell tools, web search/source checking/fetching, background
delegation, Goose memory, and Fusion tools. This is a large active tool grammar and system prompt for every turn,
even when most tools are irrelevant.

The web-search extension is especially expensive in this local single-model setup. Its `source_check` calls use
the same `chat-default` Qwen instance, register zero tools, send fresh uncached prompts of about 3,777–4,622
tokens, and recently occupied the model for 27–30 seconds per check. This can make an otherwise fast coding loop
look stalled. The extension is not intrinsically broken, but it is a poor default for normal coding sessions.

Representative capture metadata:

| Request type | Prompt/cached | Completion | Wall time |
|---|---:|---:|---:|
| Pi normal turn, 28 tools | 13,253 / 13,204 | 137 | 14.3s |
| Pi normal turn, 28 tools | 13,091 / 12,406 | 113 | 32.2s |
| Web `source_check`, 0 tools | 3,777 / 0 | 130 | 27.2s |
| Web `source_check`, 0 tools | 4,622 / 0 | 6 | 30.5s |
| Plain no-tool baseline | 1,401 / 0 | 625 | 18.0s (~34.6 tok/s) |

The relay therefore exposed two separate effects: the relay buffering bug (fixed) and real workload/model
serialization. Reasoning-off remains the correct normal chat setting; explicit Qwen/DeepSeek reasoning aliases
are available for deliberate difficult tasks.

### Current recommendation

For long coding sessions, disable the web-search/source-check extension by default and enable it only for genuine
web research. Also reduce the active Pi tool set for coding to the base tools (`read`, `bash`, `edit`, `write`)
when possible. Background delegation, Goose memory, Fusion, and web tools should be opt-in or routed to separate
models/providers rather than competing with the sole `chat-default` slot. Do not treat the previously proposed
hybrid memory architecture as established truth; it remains an option to evaluate.

No Pi laptop configuration was changed from this repo because its extension/config path has not been provided.
If the owner asks to implement the optimization, first identify the Pi extension configuration on the laptop and
make the smallest reversible change. Do not guess at a path or modify the external Pi checkout from this repo.

### Current service/config state

`chat-default` is Qwen3.8-27B, 131,072 context, reasoning off, speculative decoding enabled, KV cache q4_1,
and `parallel 1`. The explicit `reasoner` alias uses the same GGUF symlink with reasoning on/medium and a 5,000
token budget; `reasoner-alt` is DeepSeek-R1-32B. `scripts/config_drift_check.py` last reported no drift.

An independent pending change exists for the GPU1 summarizer: `config.yaml` currently has `utility-gpu` KV
cache q4_1/q4_1, which was measured at 46.8 prefill tok/s and 6.46 decode tok/s; q8_0/q8_0 measured 2602.8
prefill and 53.9 decode while still fitting beside the dispatcher. The q8_0 change is documented but must not be
applied unless the owner explicitly asks for it. The latest handoff also records a summary-prompt change and a
stale test-golden synchronization; distinguish those from live llama-swap application, which was not completed.

### Repository and verification state

The reasoning benchmark/evaluator work was committed and pushed:

```text
63a27ff docs: record reasoning alias performance findings
```

The working tree was clean at the last check. `npx tsc --noEmit`, Python byte-compilation, and `git diff --check`
passed. A later full pytest attempt stalled for approximately 150 seconds and was stopped; do not claim the full
suite passes without rerunning and resolving that stall. Use the approved operational scripts from `AGENTS.md`
for live state (`scripts/model_state.py`, `scripts/incident_snapshot.py`, and `scripts/config_drift_check.py`).

## 2026-08-24 — MTP profile application and automated sweep tooling

The Qwen3.8 separate-MTP optimization campaign added reusable agent tooling:
`scripts/benchmark_profiles/qwen38-mtp.json` stores the full 90K base profile,
`scripts/bench_sweep.py` varies only requested `--matrix` dimensions (and
`--matrix-env` environment variables), runs variants sequentially, tears down
llama-server, and writes ranked JSON summaries beside per-run JSONL logs.

Meaningful sweeps found the current best baseline: separate official MTP
sidecar, `--spec-draft-n-max 4`, `--spec-draft-n-min 0`, no `--spec-draft-p-min`,
`--spec-draft-p-split 0`, q8/q8 KV, `--flash-attn on`,
`GGML_CUDA_GRAPH_OPT=1`, backend sampling enabled, `-b 2048`, `-ub 256`,
90K context, and deterministic sampling (`temp 0`, top-p 1, min-p 0). Peak
isolated throughput was about 77.7 end-to-end tok/s. q6_K KV variants failed
to boot at 90K; Flash Attention off also failed, while CUDA graphs on was
slightly faster than off.

`config.yaml` was updated for `chat-default` and `coder` with this winning
server flag set, and `AGENTS.md`/`CLAUDE.md` document the profile and sweep
workflow. The live apply is still pending: `sudo ./scripts/restart_apply_test.sh`
could not authenticate in the non-interactive session, and the app endpoint
was down. Run that command from an authenticated terminal to regenerate and
apply the generated llama-swap config. TypeScript and Python compilation
passed; pytest needs a clean rerun after the service/config apply (a retry
stalled after the prior documented test-suite issue).

## 2026-08-24 — MTP p-min optimization matrix

At `temp 0.0`, four sequential GPU0 requests per variant were run with the
same 90K context, verified separate MTP sidecar, q8 KV, batch, and cache
settings. Results:

| Draft setting | llama.cpp eval average | Draft acceptance average | End-to-end average |
|---|---:|---:|---:|
| no `--spec-draft-p-min` | **81.80 tok/s** | 81.72% | **75.29 tok/s** |
| `--spec-draft-p-min 0.50` | 73.19 tok/s | 80.57% | 66.52 tok/s |
| `--spec-draft-p-min 0.75` | 71.57 tok/s | 89.21% | 65.15 tok/s |

For this workload, the best setting is `temp 0.0` with no
`--spec-draft-p-min`. This shallow screen was faster than the earlier full
settings average; deep Pi coding prompts still need validation.

Follow-up draft-depth sweep, same settings and four sequential requests per variant:

| `--spec-draft-n-max` | llama.cpp eval average | Draft acceptance average | End-to-end average |
|---:|---:|---:|---:|
| 2 | 76.22 tok/s | 85.65% | 70.57 tok/s |
| 3 (earlier baseline) | 81.80 tok/s | 81.72% | 75.29 tok/s |
| 4 | **83.91 tok/s** | 75.47% | **77.05 tok/s** |

The current speed winner is `--spec-draft-n-max 4`, with `temp 0.0` and no
`--spec-draft-p-min`. It improved end-to-end throughput by about 2.3% over
the n-max 3 baseline in this run, while reducing draft acceptance; validate
it against a real Pi coding prompt before making it the production default.

The reusable agent benchmark profile and matrix runner were added at
`scripts/benchmark_profiles/qwen38-mtp.json` and `scripts/bench_sweep.py`.
An automated 8-run batch sweep (`ub=256,512,1024,2048` × `b=2048,4096`)
ranked `ub=256, b=2048` first at 77.66 end-to-end tok/s; `ub=512, b=2048`
was effectively tied at 77.61, while `ub>=1024` fell sharply. A follow-up
depth sweep at `ub=256` ranked n-max 4 at 76.62, n-max 3 at 74.36, n-max 5
at 71.64, and n-max 6 at 50.68 tok/s. Keep n-max 4 and ub 256 as the
current benchmark profile; results are in `logs/benchmarks/server/*sweep.json`.

### Immediate next actions for the next chat

1. If the owner wants more evidence, inspect new files in `/tmp/pi-request-captures` and compare prompt size,
   cached tokens, tool count, completion tokens, and wall time.
2. If the owner wants the speed fix, disable web/source-check and unnecessary Pi tools on the laptop, then run a
   matched coding prompt with and without the relay; leave reasoning off.
3. If the owner asks about the summarizer, obtain explicit approval before changing/applying q8_0 KV settings;
   regenerate/apply through the approved app flow and verify with `scripts/model_state.py`.
4. Stop the temporary relay and handle its prompt-bearing captures once live testing is finished.
