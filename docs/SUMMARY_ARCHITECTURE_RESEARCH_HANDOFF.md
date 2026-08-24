# Summary architecture research handoff

**Purpose:** give the next research/brainstorming model a complete starting point for redesigning long-running
agentic coding context on this local AI harness. This is a research handoff, not an implementation plan. Do not
assume the current rolling-summary design is the desired final architecture.

## The problem to solve

The owner wants long coding projects to remain coherent while using local Qwen3.8 models with a practical context
budget below the advertised 100k+ window. The system must reduce prompt size without losing the information an
agent needs to resume programming work:

- exact file paths;
- functions, classes, symbols, and line references;
- configuration keys and values;
- commands, test names, and test results;
- failures, unresolved questions, and next actions;
- decisions and their rationale;
- distinctions between real facts, fictional/quoted material, hypotheses, and uncertain claims.

The current failure mode is not simply insufficient context. It is that a prose summary is being treated as a
trusted replacement for source history, while large messages repeatedly retrigger summarization.

## Hardware and deployment constraints

- Host: `ailab`; RTX 3090 24 GB (GPU0), RTX 3070 8 GB (GPU1), Ryzen 9, 64 GB RAM.
- `gpu0-main`: `chat-default`, `coder`, `coder-small`, `vision`; one model at a time on GPU0.
- `resident`: dispatcher and `utility-gpu` on GPU1; utility, embed, classifier on CPU.
- Qwen3.8 `chat-default`/`coder`: configured at 131,072 context because the documented MTP shape OOMed at
  262,144. `coder-small`: 30,000 configured context; native 32,768 measured.
- `utility-gpu`: 16,384 context on GPU1 to coexist with dispatcher.
- `utility-gpu` uses Qwen3-8B and is intended to be the fast summary path.
- Current working-tree change: `utility-gpu` KV cache is being aligned to `q8_0` and output cap to 1,024;
  CPU `utility` remains at 512 because its measured ~5 tok/s decode rate makes a 1,024-token output incompatible
  with the current 180-second budget. These changes still require live restart/apply validation.

## Current implementation

Relevant files:

- `app/background/summaries.py` — trigger and enqueue guard (`_in_flight`).
- `app/background/summary_policy.py` — trigger thresholds, target ordering, time budget, transcript fitting,
  and `_SUMMARY_PROMPT`.
- `app/background/summary_runner.py` — delta selection, prompt construction, GPU→CPU fallback, persistence,
  coverage metadata, and finish-reason rejection.
- `app/background/summary_coverage.py` — covered-prefix and summary SHA-256 trust boundary.
- `app/chat/context.py` — exact summary/raw partition and safe refusal on context overflow.
- `app/chat/turn.py` — context assembly and summary recovery enqueue on overflow.
- `data/app.db` — `chats.summary`, `messages`, `traces`, and `spans`.

Current flow:

1. After a chat turn, `on_turn_complete()` calls `maybe_enqueue_summary()`.
2. If real `llm_stream` usage exists, the trigger is approximately 50% of the current model context, floored by
   the smallest routable context. Without usage, it falls back to every six user turns.
3. A guarded background job reads messages after the trusted covered prefix.
4. `fit_to_budget()` estimates tokens using characters / 3.5 and tries to keep the oldest new messages.
5. It tries `utility-gpu`, then CPU `utility`.
6. The system prompt is `_SUMMARY_PROMPT`; the user prompt is either `Conversation so far: ... Updated summary:`
   or `Previous summary: ... New messages since the previous summary: ... Updated summary:`.
7. The result is committed only when non-empty and `finish_reason == "stop"`.
8. Coverage count and SHA-256 fingerprints are stored in the summary span. This proves structural coverage, not
   semantic preservation of identifiers.

Current universal prompt:

```text
Update the rolling summary of this conversation so that the same agent or user could resume exactly where things
left off -- this is working context for continuing the task, not a synopsis for an outside reader. You are given
the previous summary and the messages that happened since it was written. Produce an updated summary that
preserves everything still relevant from the previous summary and folds in what's new. Output only the summary
text -- no preamble or labels.

Structure it with these sections, omitting any that are empty:
- Entities and specifics stated (exact names, numbers, dates, quoted facts)
- Decisions made and why
- Commitments or constraints stated
- Open questions / next steps

Preserve numbers, dates, proper nouns, and quoted phrases verbatim -- do not round, generalize, or drop them even
if space is tight.
```

The wording is content-agnostic, but the phrase “facts”/“entities and specifics” can make a fictional essay sound
like verified real-world fact. Production summary spans currently store the user transcript but not the system
prompt; the quality harness does store both.

## Confirmed incident evidence

### Trace family `c63a484a10f04da8a94d7bde9c816b6e`

Original trace family:

- `5079b1c3-657d-41c8-93e9-b1aa8e86ccc7`: GPU summary timeout after 180 seconds on a 33,914-character Vael
  Archipelago essay.
- `89079391-4e3d-45b1-b47f-639b16e5e014`: CPU fallback timeout after 180 seconds; `time_budget_tokens` was
  3,442. This was an input-time budget, not a 4k model context. The oversized first message was still admitted
  whole by `fit_to_budget()`.
- `7388de1f-bdcc-4831-b48f-dfc9f00972a6`: GPU retry ended with `finish_reason='length'` after about 72 seconds.
- Corresponding CPU retry also ended with `finish_reason='length'`.
- No summary was committed for that chat.

The same family also contained `6f512e48-490b-473b-a272-4531d1cf914e`, a separate `coder-small` follow-up that
hit the 90-second first-token timeout with an approximately 9,889-token prompt.

### Trace family `da2cd7d6eabb42aab195a200d24c3a3e`

Trace `2904259a-92a5-454f-b4d5-000cf0bf213e` showed what looked like a summary loop:

- One large user turn: approximately 7,786 prompt tokens.
- `coder-small` answered successfully.
- `684b9212-0423-4c99-a8db-a1fc6363b49b`: first summary, `utility-gpu`, about 14 seconds, covered 2 messages.
- User then sent `I need more details`.
- `coder-small` answered with approximately 7,932 prompt tokens.
- `3189be16-dc1b-49b2-8cde-8e6da2ad7285`: second summary, `utility-gpu`, about 10.5 seconds, covered 4 messages.

This was not an autonomous infinite loop. Each large user turn crossed the token trigger, so the current policy
correctly—but perhaps too eagerly—scheduled a summary after each turn. A giant initial document keeps future
prompts above the threshold.

The new prompt was present in behavior: both summaries used the headings `Entities and specifics stated`,
`Decisions made and why`, `Commitments or constraints stated`, and `Open questions / next steps`. It is working as
written, but it still compresses a detailed essay into a high-level recap and does not classify claims as fictional,
quoted, hypothetical, or verified.

## Benchmark caveat

The Aug 23–24 summarizer tests were realistic but used `max_tokens=512` because that matched production at the
time. The payments-retry test was a synthetic 12-turn programming transcript (~3,076 input tokens) with planted
paths, symbols, test names, config values, and next steps. It was not a real repository, although it modeled real
agentic coding content. Some variants hit the 512 cap. The Vael production prompt was also tested with a 512-token
cap. Therefore, those tests establish relative prompt behavior and q8_0 speed/quality parity, but do not prove that
the summarizer can produce sufficiently complete 1,024+ token coding checkpoints.

## Pi agent-loop performance finding (2026-08-24)

Pi (`earendil-works/pi`) was observed through a temporary local relay while using the Qwen3.8-27B model. The
initial 12–14 tok/s experience was **not** caused by a huge uncached prompt: representative tool-loop calls had
about 7,376–8,932 prompt tokens, of which 7,041–8,562 were cached. Pi sends its tool schemas each turn and keeps
assistant `reasoning_content` in history, but those facts did not explain the threefold wall-time difference.

The decisive matched workflow comparison was server-side reasoning enabled versus disabled for the normal chat
alias. With reasoning on, Pi made 10 calls, generated 4,267 completion tokens, and took 205.5s. With reasoning
off, it made 7 calls, generated 1,462 completion tokens, and took 63.8s: **3.2x faster end-to-end**. Removing
`ngram-mod` speculation did not produce a material tool-loop improvement. Tool-call grammar can still add
per-turn variance, but the evidence identifies default reasoning as the primary cost.

Operational conclusion: keep `chat-default` reasoning off for interactive and coding-agent work; reserve an
explicit thinking alias for deliberate hard problems. This isolates performance policy from the separate durable
memory/compaction architecture decision.

The resulting live aliases are:

- `chat-default`: Qwen3.8-27B, 131,072 ctx, reasoning off.
- `reasoner`: the same Qwen blob through a distinct symlink, 131,072 ctx, medium reasoning and a 5,000-token
  reasoning budget. The separate path prevents llama-swap deduplication with `chat-default`.
- `reasoner-alt`: DeepSeek-R1 32B, 8,192 ctx.

A five-prompt manual quality/latency check (r1, r2, r5, r6, r7 from
`scripts/eval_data/reasoner_prompts.json`, `--n-predict 5120`) found both reasoning aliases correct. Selecting
the final non-duplicate Qwen runs: 111.3s and 3,081 completion tokens (~27.7 end-to-end completion tok/s);
DeepSeek: 141.3s and 3,243 (~23.0). This is a small controlled comparison only. `r3` was excluded because its
fixture ground truth is mathematically incorrect, and `r4` is open-ended. Raw transcripts are in
`logs/benchmarks/quality/reasoner.jsonl`.

## Architectural direction to research

Compare the current “one rolling prose summary” design with a durable project-state architecture:

```text
repository + git + test logs + artifacts = authority
structured project checkpoint = resumable state
recent turns = conversational continuity
retrieval = exact evidence on demand
rolling prose summary = orientation only
```

Potential checkpoint fields:

- active task and acceptance criteria;
- files/paths touched;
- symbols/functions/classes changed;
- exact config changes;
- commands and tests run, with results;
- errors and unresolved risks;
- decisions and rationale;
- current implementation state;
- next concrete actions;
- references to raw messages, artifacts, diffs, and logs.

Research should compare:

1. SQLite structured state versus filesystem checkpoint files under `projects/<id>/state/`.
2. LLM-generated extraction versus deterministic extraction of paths, symbols, commands, and test names.
3. Raw-message retention, keyed facts, source-segment retention, or a hybrid.
4. Triggering on actual assembled context pressure versus delta-token accumulation.
5. One universal prompt versus model-alias/content-adaptive prompts, including collapse risk.
6. Sentinel/identifier validation and retry behavior.
7. How opencode sessions should publish checkpoints and consume project state.
8. How to prevent summary-trigger loops and duplicate concurrent summary jobs.
9. Whether 1,024–1,536 output tokens is adequate for checkpoints on this hardware.

## Desired research output

The next model should return:

- a recommended architecture for long agentic coding sessions;
- a concrete context-layer budget for Qwen3.8;
- the minimum schema/files needed for durable project state;
- a safe compaction/checkpoint algorithm;
- validation rules for coding summaries;
- a migration path from `chats.summary` without breaking frozen chat/SSE contracts;
- benchmark cases that distinguish prose, code, config, numbered facts, and large pasted documents;
- explicit tradeoffs for this 3090/3070 setup;
- a list of changes that require owner approval (schema, config keys, frozen contracts, or new dependencies).

Do not recommend simply increasing the context window or trusting a larger summarizer. The central requirement is
that exact coding state remains recoverable even when the language-model summary is lossy.

## Canonical investigation commands

Use the repository’s diagnostic scripts rather than ad-hoc queries:

```bash
python3 scripts/trace_inspect.py <trace_id> --stdout
python3 scripts/incident_snapshot.py <trace_id> --with-trace
python3 scripts/model_state.py
python3 scripts/config_drift_check.py
python3 scripts/chat_ctx_budget.py <chat_id>
```

Relevant source and history:

- `docs/HANDOFF.md` — chronological investigation history.
- `docs/current_bugs.md` — audit findings and summary-quality experiments.
- `docs/AGENT_CONTEXT_MEGA.md` — hardware, placement, and operational context.
- `app/background/summary_policy.py` and `summary_runner.py` — current implementation.
- `logs/traces/` and `logs/incidents/` — generated trace investigations.
