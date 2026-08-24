Read-only audit complete. No application code was changed; no branch created.

  The major performance incident is real, but routing is not the cause of e84…: it was a manual chat-default override. The 17.64 tok/
  s trace had a 6,036-token full prefill with cache_n: 0, hidden reasoning, and poor speculative acceptance (46%), versus the 43-
  token baseline’s 61 tok/s. The live prompt was malformed by context handling, not a Qwen model-flag regression.

  Confirmed critical defects:

  - P0 — summary compaction silently deletes uncovered history. The summary covered messages 1–25, but compaction retains only the
    last 12 raw messages; 26–35 vanished from the model prompt. This exactly explains the major loss in the traced chat. app/chat/
    orchestrator.py:113

  - P0 — giant messages break summarization and can silently remove the current request. _fit_to_budget() always admits an oversized
    first message, so an 8k-token item is sent whole to the summarizer; it timed out repeatedly for 180s. Normal chat truncation also
    drops whole messages, including the newest user message if it alone exceeds budget. There is no request-size/per-message policy.
    app/background/summaries.py:249 app/chat/api.py:52

  - P0 — context-overflow retries duplicate user messages. `execute_turn()` persists the user message before assembling/checking the
    model context; a failed `hey` retry in traces `f2713790-4be4-4b5c-9a30-137fb7113041` and
    `b73662e9-cd77-4cef-beab-d65fd8a0599b` left two identical user rows and no assistant response. Overflow recovery then submitted
    the 33,866-character history to `utility-gpu`, which timed out after 180s in trace `0cc725d0-15a6-4296-8fb9-cf0bbb16b8b2`.
    app/chat/turn.py:169 app/background/summary_runner.py:52

  - P0 — configured context must be deployable with the model's full runtime shape, not merely its advertised window. On 2026-08-23,
    `coder` at 262,144 context OOMed while its MTP speculative decoder allocated a second 1GiB KV buffer; `utility-gpu` can expose
    40,960 native tokens in isolation but consumes ~7.8GiB, leaving no room for the GPU1 dispatcher. Context changes require isolated
    capacity/decode tests *and* live co-residency validation. config.yaml models.coder/models.utility-gpu

  - P0 — poisoned summaries are accepted as truth. A refusal/echo can be stored as the rolling summary with no quality validation,
    then fed back into every later prompt. The existing title path has echo detection; summaries do not. app/background/
    summaries.py:201

  - P0 follow-up investigation — summary quality is unsafe for real-world, index-heavy conversations. In trace
    `cee82d55-be7d-446a-a316-d42810853a4a`, a successful `utility-gpu` summary marked all 9 prior messages covered but reduced
    a 45-item numbered fact list to a topic-level synopsis. Subsequent prompts therefore omitted the original exact mappings;
    `coder-small` answered FACT 7 with the wrong item, and `chat-default` later admitted FACTs 8–11 were unavailable. Investigate
    structured-list preservation, keyed fact extraction, summary quality/sentinel validation, and a policy to retain raw messages
    when a lossy summary cannot preserve exact user-editable data. Add real-world manual cases to the regression plan before
    treating coverage metadata alone as sufficient.

  - P0 — periodic warmup likely destroys the only reusable chat KV prefix every five minutes. After startup it pings every resident
    model unconditionally. With --parallel 1, that includes chat-default’s sole slot, competing with traffic and replacing its active
    prompt context. This was not the direct cause of e84—the sweep occurred afterward—but it is a systematic cache-reuse regression.
    app/main.py:100 app/warmup.py:78

  - P1 — attachments are a complete black hole. The API accepts attachments, but never passes them to handle_message; even if it did,
    the public type is list[str] while the router only recognizes structured {type: ...} attachments. The attachment-routing tests
    bypass the actual API. app/chat/api.py:124 app/router/rules.py:18

  - P1 — invalid/disabled model aliases can be selected and persisted. /model accepts any string; send-message does too. The model
    list and picker include disabled entries. Failure then occurs later as an opaque llama-swap error. app/chat/api.py:97 app/
    settings/api.py:107

  - P1 — GPU0 rewarm is unwired. mark_gpu0_activity() has no caller, so the intended rewarm-after-nondefault policy never triggers.
    Its task is also untracked and not cancelled during shutdown. app/gpu/rewarm.py:23 app/main.py:141

  - P1 — forced compaction does not guarantee safety. At 20 seconds it proceeds with stale/truncated context, while leaving an
    untracked 180-second task running outside the background queue. That task has no queue retry or shutdown ownership. app/
    background/summaries.py:650

  - P1 — summary retry has a per-chat race. The guarded job clears _in_flight between the queue’s first failed attempt and retry,
    allowing a second summary for the same chat to be enqueued and race to overwrite coverage/summary state. app/background/
    summaries.py:421 app/background/queue.py:63

  - P1 — model_loading / swap_wait is false telemetry. It is emitted after any two-second first-token delay, including long warm
    prefill. The existing incident record proves this with a loaded model and 75s prompt processing. app/chat/orchestrator.py:497
    docs/DEBUG_FINDINGS_router_swap.md:46

  - P1 — GPU apply can claim success while residents are still cold. It returns {"ok": true} after three attempts even if
    all_residents_loaded() remains false. app/gpu/api.py:152

  - P1 — config overlays can silently preserve stale full model rosters. Updating one model writes the entire roster into
    settings.local.yaml; that overlay currently enables reasoner-alt while checked-in config.yaml disables it. This may be
    intentional, but it proves base config changes are being shadowed wholesale. app/settings/store.py:55 settings.local.yaml

  Other important audit findings:

  - No cross-field config validation: routing/default/background aliases can point to absent or disabled models.
  - LLMClient.close() is never called during app shutdown; both normal and summary clients can leak transports. app/llm_client.py:211
  - One SQLite connection is used concurrently from the executor without transaction serialization; this is a concurrency risk under
    parallel streams/jobs. app/db.py:45

  - Debug trace listing has no limit bounds; negative limits can return all traces/spans. app/debug/api.py:79
  - The app binds 0.0.0.0, exposes stored prompts/debug traces and GPU config apply with no auth. This is high severity if the host
    is reachable beyond a trusted LAN.

  - The dirty compacting SSE event changes a frozen event vocabulary without owner approval.

  Checks: compileall and npx tsc --noEmit pass; 176 pytest tests collect successfully. The full suite did not finish within the
  audit’s 30-second execution windows, so I am not claiming it passes.

  The first implementation batch should be context integrity: complete-coverage compaction invariant, oversized-message policy,
  summary validation, and cache-safe warmup.

  - 2026-08-23 — utility-gpu q8_0 KV-cache fix: speed confirmed, quality parity confirmed (not yet applied to config.yaml).
    Ran the exact real production prompt from trace `b6c56923-0ffe-4080-8b25-b73ddd05c1e4` (chat
    `f524f4b2c1a94fd598ca373f78e6ee9d`, 34,451-char prompt, 7,964 prompt tokens) against an isolated GPU1 llama-server
    (solo-pinned, same flags as the shipped `utility-gpu` entry except `--cache-type-k/v q8_0` in place of `q4_1`),
    via `scripts/eval_quality_transcripts.py` plus a direct request reproducing the exact production
    `messages` shape (system `_SUMMARY_PROMPT` + user transcript — the span's recorded `prompt` field only
    captures the user message, so the tool-only run silently drops the system prompt and produces degenerate,
    unstructured output; the corrected system+user request is the valid comparison). **Speed**: 9.06s wall,
    7,964 prompt tokens / 341 completion tokens — vs the recorded q4_1 baseline's 53.2s / 7,964 / 358 — a ~5.9x
    speedup on the identical token counts, consistent with the isolated benchmark already in
    docs/HANDOFF.md's 2026-08-23 entry. **Quality**: parity confirmed. The q8_0 response reproduced the same
    four-section (Key facts/Decisions/Preferences/Open questions) structure as the q4_1 response and preserved
    the same level of topical fidelity (Victorian field-guide premise, 1847-1853 timeframe, ecological/cultural
    scope). It also **dropped the exact same concrete numeric details** the q4_1 response already dropped
    (eleven major islands, four hundred leagues from the Thornwall Coast, nine hundred fathoms depth, the 1791
    coral-harvest decline date) — same lossy pattern, not a new or worse loss. Switching KV-cache precision from
    q4_1 to q8_0 did **not** measurably change summary content/quality on this prompt; it only changed speed.
    Caveat: single real-prompt test, not a multi-sample statistical comparison — the q8_0-without-system-prompt
    run (tool-only, logged to `logs/benchmarks/quality/summarizer.jsonl` as `utility-gpu-q8_0` and
    `utility-gpu-q8_0-rerun`) shows real run-to-run variance (one run echoed the prior assistant turn verbatim,
    a rerun produced a full but unstructured narrative) when the system prompt is missing, so the fix should be
    re-verified once applied live through llama-swap with a couple of real chats, not just this one isolated
    repro. Recommended change (`config.yaml` `models.utility-gpu.extra_flags`) still awaiting explicit owner
    apply per docs/HANDOFF.md.

  - 2026-08-23 — coding-session continuability test: summaries lose file paths and root-cause line
    numbers, keep next-step function names. Built a synthetic ~12-turn, 3,076-prompt-token realistic
    coding-session transcript (fixing a duplicate-charge bug in a fictional `services/payments/retry.py`)
    and ran it through the real production summary prompt (`_SUMMARY_PROMPT` from
    `app/background/summary_policy.py` + `run_summary()`'s exact `"Conversation so far:\n{transcript}"` /
    `"Updated summary:"` framing, first-summary shape) against an isolated GPU1 `utility-gpu` server with
    the q8_0 KV-cache fix from the entry above (`--cache-type-k/v q8_0`, `-c 16384 -n 512 --reasoning off`,
    solo-pinned `CUDA_VISIBLE_DEVICES=1`), via `scripts/eval_quality_transcripts.py --class summarizer`.
    9.06s→**6.5s wall, 3,076 prompt tokens / 359 completion tokens** (logged to
    `logs/benchmarks/quality/summarizer.jsonl` as `utility-gpu-q8_0-coding-session`). Checklist of every
    identifier deliberately planted in the transcript, checked against the summary output:

    | Planted identifier | Survived? |
    |---|---|
    | File path `services/payments/retry.py` | **No** — never appears anywhere in the summary |
    | Function `submit_payment_with_retry()` | No |
    | Root-cause line `_should_retry()` line 142 off-by-one | No — the off-by-one fix is dropped entirely |
    | Root cause: idempotency key regenerated per-attempt (the actual duplicate-charge cause) | Yes, conceptually ("regenerating the idempotency key on each retry attempt") — but with no file/function name attached |
    | Function `_parse_retry_after()` | Yes, named explicitly in Open questions |
    | Function `_compute_backoff_delay()` | Yes, named explicitly in Open questions |
    | Decision: exponential backoff+jitter over fixed delay, *because* of the thundering-herd incident 3 weeks ago | Partial — "avoid thundering-herd problems" rationale kept, the specific prior-incident referent dropped |
    | Config key `config/payments.yaml retry.max_attempts` 3→5 | Partial — the 3→5 value change kept, the config file/key path dropped |
    | Test `tests/payments/test_retry_backoff.py::test_respects_retry_after_header` (+2 other named tests) | No — only a vague "test suite was added" survives, no file path or test names |
    | Next step: test for malformed Retry-After fallback | Yes |
    | Next step: add max-delay cap to `_compute_backoff_delay()` | Yes, with the function name intact |
    | Next step: update `docs/payments-runbook.md` | Yes, with the file path intact |
    | Nice-to-have metric `payment_retry_outcome_total` | Partial — "pending metric" concept kept, exact metric name dropped |
    | `config/payments.local.yaml` override-check (confirmed clean, no shadowing) | No — dropped entirely |

    **Verdict: partial, not safe to rely on alone.** An agent resuming from this summary would correctly
    know *what* was fixed (duplicate charges via idempotency-key-per-attempt) and get two of three
    next-steps with real function names to grep for (`_compute_backoff_delay()`,
    `docs/payments-runbook.md`) — genuinely useful. But it would have **no file path to the code it needs
    to edit** (`services/payments/retry.py` never appears), would not know the fix already touched
    `_should_retry()`'s line-142 off-by-one (could re-investigate or reintroduce it), and would have no
    test file path to extend for the still-open malformed-Retry-After test. This matches the pattern
    already on record above (numbered/indexed facts survive as vague topic mentions, not verbatim) but
    sharpens it for the coding use case specifically: **prose/decision rationale survives; the two
    things an agent needs most to resume work without re-discovery — file paths and exact
    already-fixed-vs-not code locations — are the first things dropped.** Confirms the standing
    recommendation (structured/keyed-fact extraction, or retaining raw messages for file-path/line-number
    bearing content) should explicitly include source file paths and function/line identifiers as a
    protected category, not just numbered lists. GPU1 confirmed idle (`nvidia-smi` 0 MiB) after the test
    server was killed.

  - 2026-08-23 — summarizer prompt-variant comparison: a restructured coding-specific prompt
    (variant B) meaningfully improves identifier recall with no regression; a "be maximally
    exhaustive" instruction (variant D) makes things clearly worse. Re-ran the identical
    payments-retry coding transcript (12-turn, 3,076-token, same identifiers as the entry above)
    against the current production `_SUMMARY_PROMPT` verbatim plus four candidate rewrites, all as
    the `system` message via `scripts/eval_quality_transcripts.py --class summarizer`, one
    `--model-label` per variant, same isolated GPU1 `utility-gpu` server (q8_0 KV-cache fix,
    `-c 16384 --reasoning off`, solo-pinned `CUDA_VISIBLE_DEVICES=1`, `max_tokens=512` matching
    `config.yaml`), logged to `logs/benchmarks/quality/summarizer.jsonl` as
    `promptexp-{baseline,variant-a-continuity,variant-b-restructured,variant-c-hybrid-codereferences,variant-d-exhaustive-BAD}`.
    Note: this run's baseline scored noticeably better than the baseline documented in the entry
    above on some items (file path and config path both survived here) — same prompt/server/transcript,
    different sample, so treat item-level pass/fail as noisy and the *relative* ranking across variants
    (same run, same seed conditions) as the reliable signal.

    | # | Checklist item | Baseline | A: +continuity note | B: restructured sections | C: hybrid +Code refs | D: "be exhaustive" |
    |---|---|---|---|---|---|---|
    | 1 | File path `services/payments/retry.py` | Pass | Pass | Pass | Pass | Pass |
    | 2 | Function `submit_payment_with_retry()` | Fail | Pass | Pass | Pass | Fail |
    | 3 | `_should_retry()` line 142 off-by-one | Partial (fn name, no line #) | Partial | Partial | **Pass (exact `retry.py:142` and `:118`)** | Partial |
    | 4 | Idempotency-per-attempt root cause | Pass | Pass | Pass | Pass | Pass |
    | 5 | Function `_parse_retry_after()` | Pass | Pass | Pass | Pass | **Fail** |
    | 6 | Function `_compute_backoff_delay()` | Pass | Pass | Pass | Pass | Pass (barely, in truncated tail) |
    | 7 | Backoff+jitter decision + rationale | Partial (no "3 weeks ago" incident) | Partial | Partial | Partial | Partial |
    | 8 | Config key+path `config/payments.yaml retry.max_attempts` 3->5 | Pass | Pass | **Pass (+ `base_delay_s` bonus)** | Pass | **Fail (value kept, path dropped)** |
    | 9 | Test file/names (`tests/payments/test_retry_backoff.py::...`) | Fail | Fail | **Pass (2 exact names + path)** | **Pass (3 exact names + path)** | **Fail** |
    | 10 | All 3 next-steps present | Partial | Pass | Pass | Pass (near token cap) | **Fail (truncated, only 2/3)** |
    | 11 | Metric `payment_retry_outcome_total` | Pass | Pass | Pass | **Fail (name dropped, concept kept)** | **Fail** |
    | 12 | `payments.local.yaml` override-check | Fail | Fail | Fail | Fail | Fail |

    **Speed/budget** (wall-clock / prompt tok / completion tok, all against the same 3,076-token
    transcript): baseline 8.21s / 3076 / 471 · A 7.64s / 3111 / 429 · B **6.70s / 3083 / 366** ·
    C 8.92s / 3123 / **512 (hit the cap)** · D 8.96s / 3107 / **512 (hit the cap, truncated mid-list)**.

    **Recommendation: apply variant B.** Its system prompt:

    > Update the rolling summary of this conversation. You are given the previous summary and the
    > messages that happened since it was written. Produce an updated summary that preserves
    > everything still relevant from the previous summary and folds in what's new. Output only the
    > summary text -- no preamble or labels.
    >
    > Structure it with these sections, omitting any that are empty:
    > - Files and locations touched (exact paths/functions/lines)
    > - Changes made and why
    > - Tests added/needed
    > - Config changed
    > - Next steps

    It replaces the generic four-section structure with coding-specific sections (no extra
    "verbatim" instruction needed — restructuring the sections themselves did the work) and was the
    only variant that got *both* the exact test file+test names *and* the exact config key+path,
    while using the **fewest** completion tokens and **fastest** wall-clock of any variant tested —
    a compact, coding-shaped structure is not just accurate but cheap. Its one shortfall is explicit
    line numbers (142/118), where variant C ("Code references" bonus section) does better —
    worth a follow-up test combining B's section structure with a lightweight "include line numbers
    when mentioned" nudge, but not blocking; B alone is a strict improvement over the current
    production prompt on every item it doesn't tie.

    **What made things worse (on record, don't retry this direction): variant D**, which added
    "Be as thorough and exhaustive as possible -- capture every detail... so that nothing is lost."
    This backfired hard: it spent the token budget on discursive prose, hit the 512-token cap, and
    got cut off mid-list — losing `submit_payment_with_retry()`, `_parse_retry_after()`, *all* test
    file/names, the config file path, the metric name, and one of three next-steps entirely, several
    of which even the unmodified baseline preserved. Verbosity instructions for a rolling summary
    fight the fixed token budget and lose the identifiers first, not last — the opposite of the
    intended effect. Do not add "be exhaustive/thorough" style instructions to this prompt.

    GPU1 confirmed idle (`nvidia-smi` 1 MiB) after the test server was killed.

## 2026-08-24 — general-purpose ("resume exactly") prompt vs. coding-shaped prompt: content-mismatch collapse found, universal candidate recommended

Followed up the just-applied coding-shaped `_SUMMARY_PROMPT` (Variant B, `app/background/summary_policy.py`,
applied 2026-08-24) with the general-content half of the same question: the router also sends
`chit_chat`/`reasoning_task`/`vision_task`/plain `chat` turns through the identical single summarizer
prompt, and Variant B was never validated against prose content. Reconstructed the real Vael Archipelago
essay case (chat `f524f4b2c1a94fd598ca373f78e6ee9d`, exact `Conversation so far:\n...\n\nUpdated summary:`
user-message shape from `scripts/eval_quality_transcripts.py`'s system-field-aware harness, reusing
`q4_1_prompt.txt` from the 2026-08-23 q8_0 test — 34,451-char/7,964-token real production prompt) and the
payments-retry coding transcript (12,655-char/~3,076-token, same as the prior prompt-variant entry) as the
two content types. Server: isolated GPU1 `utility-gpu` (`qwen3-8b.gguf`, `CUDA_VISIBLE_DEVICES=1`
solo-pinned, `-c 16384 --reasoning off --cache-type-k/v q8_0` — production flags), all 8 combinations sent
in one `eval_quality_transcripts.py` batch (`--class summarizer --model-label genpromptexp`,
`max_tokens=512` matching config), logged to `logs/benchmarks/quality/summarizer.jsonl`.

**Three general-purpose candidates tried** (all replace Variant B's coding sections with content-neutral
ones, same "resume exactly" / delta-fold framing kept from the production prompt):
- **G1**: reframes the objective explicitly ("so that the same agent or user could resume exactly where
  things left off... not a synopsis for an outside reader") + generalized sections — Entities and specifics
  stated (exact names, numbers, dates, quoted facts) / Decisions made and why / Commitments or constraints
  stated / Open questions / next steps.
- **G2**: G1 plus one added sentence: *"Preserve numbers, dates, proper nouns, and quoted phrases verbatim
  -- do not round, generalize, or drop them even if space is tight."*
- **G3**: a stronger rewrite of the framing itself (leads with "you are producing working context... not a
  summary for an outside reader") with slightly reworded section names.

**Comparison matrix** (checklist items from the two known failure cases; baseline = old pre-2026-08-24
generic four-section prompt, codeB = the currently-shipped Variant B):

Prose content (Vael Archipelago, checklist = eleven islands / four hundred leagues / nine hundred fathoms / 1791):

| Variant | eleven islands | 400 leagues | 900 fathoms | 1791 | completion tokens |
|---|---|---|---|---|---|
| baseline (old generic) | Pass | Fail | Fail | Fail | 512 (truncated) |
| **codeB (coding-shaped, shipped)** | **Fail** | **Fail** | **Fail** | **Fail** | **32 — degenerate, empty section headers only, no content** |
| G1 | Pass | Pass | Pass | Fail | 488 |
| G2 | Pass | Pass | Fail | Fail | 465 |
| G3 | Pass | Fail | Pass | Fail | 512 (truncated) |

Coding content (payments-retry transcript, 10-item checklist from the 2026-08-23 prompt-variant entry):

| Variant | pass count /10 | notable |
|---|---|---|
| codeB (coding-shaped, shipped) | 7/10 | strong, as previously documented, but this run missed `submit_payment_with_retry()` and the metric name (sampling variance — no `--temp` pinned, matches production's unpinned sampling) |
| **G1 (no verbatim nudge)** | **0/10** | **degenerate — 37 completion tokens, empty section headers only, no content** |
| G2 (+ verbatim nudge) | 8/10 | beat codeB on this run: got `submit_payment_with_retry()` and the metric name that codeB missed |

**The headline finding is not a scoring difference — it's a collapse mode.** `codeB` on prose and `G1` on
coding both produced a bare list of section headings with zero body content (32 and 37 completion tokens
respectively, vs 400-500+ for every substantive response) — the model appears to short-circuit when a
rigid, content-specific template doesn't fit what it's given (e.g. asked for "Files and locations touched"
against a 19th-century nature essay with no files). That's a worse failure than lossy-but-present prose:
it's total information loss for that regen, silently written to `chats.summary` with no validation (the
still-open P0 from the audit findings in this file). **G2 — the general prompt with the added verbatim-nudge
sentence — was the only candidate that avoided the collapse on both content types** and scored competitively
with (in coding's case, better than) the content-specific prompt on its own home turf.

**Recommendation: prefer G2 as a single universal prompt over content-adaptive selection**, reversing the
default assumption going in. Reasoning: (1) G2 matched or beat the specialized coding prompt on coding
content in this run while also handling prose well — so adaptive selection buys little accuracy upside here;
(2) content-adaptive selection adds a real new failure mode this test just demonstrated — a rigid
content-specific template misapplied to mismatched content doesn't degrade gracefully, it collapses to an
empty summary — so a misclassification (or a chat that mixes code and prose) under an adaptive scheme could
silently produce *worse* summaries than doing nothing content-adaptive at all. A single template that stays
generic enough to fit any content (G2's approach) structurally can't hit that failure mode. (3) Simplicity:
no new signal-joining logic needed in `summary_runner.py`.

**If the owner still wants adaptive selection despite the above** (e.g. to chase codeB's edge on exact line
numbers like `retry.py:142`, which no variant nailed this run), the cheapest signal available without new
infrastructure: `summary_policy.latest_usage()` already joins `spans`/`traces` by `chat_id` to read the most
recent `llm_stream` span's `model` field — that model *alias* (`coder`/`coder-small` vs
`chat-default`/`reasoner`/`vision`) is a free proxy for the router's classifier decision on the turns being
folded in, with no new DB column or trace/span schema change, just an additional lookup alongside the
existing usage-threshold query in `summary_runner.run_summary`. A raw-text heuristic (code fences / file-path
regex over the transcript about to be summarized) would be simpler still (no span join) but noisier — a
prose chat that merely quotes a file path once would misfire. Recommend the model-alias signal if this path
is ever taken. **Not implemented — this changes `summary_runner.py` control flow and needs the owner's
explicit sign-off, same as the standing note on `summary_policy.py`.**

**Winning prompt (G2), ready to apply if/when the owner approves a universal-prompt swap:**

> Update the rolling summary of this conversation so that the same agent or user could resume exactly where
> things left off -- this is working context for continuing the task, not a synopsis for an outside reader.
> You are given the previous summary and the messages that happened since it was written. Produce an updated
> summary that preserves everything still relevant from the previous summary and folds in what's new. Output
> only the summary text -- no preamble or labels.
>
> Structure it with these sections, omitting any that are empty:
> - Entities and specifics stated (exact names, numbers, dates, quoted facts)
> - Decisions made and why
> - Commitments or constraints stated
> - Open questions / next steps
>
> Preserve numbers, dates, proper nouns, and quoted phrases verbatim -- do not round, generalize, or drop
> them even if space is tight.

Caveat: single-run comparison with unpinned sampling temperature (matches production, but means item-level
pass/fail on any one item is noisy — treat the collapse-vs-no-collapse finding as the reliable signal, per
the standing guidance in the entry above). `_SUMMARY_PROMPT` in `app/background/summary_policy.py` was
**not** touched this session (still Variant B, coding-shaped, as applied 2026-08-24) — this is a design/test
exercise only, pending owner sign-off on either swapping to G2 universally or adding adaptive selection.
GPU1 confirmed idle (`nvidia-smi` 1 MiB) after the test server was killed.
