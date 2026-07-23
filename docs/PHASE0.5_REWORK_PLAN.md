# Phase-0.5 Rework — Fix Token-Budget Bug, Add Hammer-vs-CPU-Titler Test, Rewrite Summary Doc

## Context

Test 4 (reasoner/coder quality eval) capped `max_tokens` at 1024 for the two
32B-class GPU-resident reasoners on a 24GB card with nothing else consuming
serious budget — too tight for thinking-mode models, causing DeepSeek-R1-32B
and Qwen3.6-35B-A3B-thinking to blow their entire budget on `<think>` and
return an empty answer on most multi-step prompts. The user correctly
called this out: these are big models sitting mostly alone on the 3090, so
the fix is to **raise the ceiling substantially** (not "add retry logic" —
that's a production concern for later, flagged as a future test, not a fix
to this eval), re-run, and get real quality numbers instead of budget-choked
ones.

Separately, the user wants a **new test**: Hammer2.1-1.5b as a title
generator vs. whatever CPU-resident model would otherwise do titling —
faster+more-accurate wins, decided by data, not assumed.

Finally, both `docs/phase0-measurements.md` and `docs/PHASE0_FINDINGS_SUMMARY.md`
have a needle/Cactus-research problem: paragraphs of external-research
narrative that drowns the actual roster verdicts. `phase0-measurements.md`
itself is fine per the user ("very good") — it needs the needle sections
trimmed, nothing else. `PHASE0_FINDINGS_SUMMARY.md` is the real problem:
outdated (written before Phase-0.5 Tests 1-6), redundant with
phase0-measurements.md's own detail, and full of prose duplicating what's
already stated elsewhere. It needs a full rewrite: concise, one place per
finding, a single verdict table, nothing repeated across sections.

## Part 1 — Re-run Test 4 with a real token budget (GPU, one job at a time)

Re-run using the same fixture files already built this session
(`scripts/eval_data/quality_prompts.json`, `reasoner_prompts.json`,
`vision_prompts.json`, `coder_debug_prompts.json`) and the same scripts
(`scripts/eval_quality_transcripts.py`, `scripts/eval_coder_compile.py`) —
no script changes needed, just a different `--n-predict`:

- **Reasoner A (DeepSeek-R1-32B) and Reasoner B (Qwen3.6-35B-A3B thinking)**:
  re-run the 7 reasoner prompts and 6 coder_debug prompts at
  `--n-predict 4096` (up from 1024) — large enough that a thinking trace
  plus answer fits for every prompt in the set based on what we saw
  (longest truncated trace hit exactly 1024; 4096 gives 4x headroom without
  meaningfully hurting real usage, since these are background-ish
  reasoning tasks, not the fast-path).
- **Coder Q5_K_M debug-diagnosis set**: already finished within budget at
  768 tokens (no truncation observed) — no re-run needed, keep the
  existing `coder_debug.jsonl` rows for coder-q5-30b-a3b as-is.
- Overwrite the reasoner/coder_debug JSONL rows for the two reasoner labels
  (the eval scripts append, so either delete-and-rerun the two labels'
  rows or start fresh output files — confirm which before running so old
  budget-choked rows don't linger next to new ones).
- Re-judge the transcripts (same rubric as before) and **rewrite** the
  Reasoner A vs B section of `docs/phase0-measurements.md` §9 with the real
  numbers — remove the old "5/7 truncated" framing entirely if the retest
  shows both finishing reliably; keep it only if truncation genuinely
  still occurs at 4096 (report honestly either way).

## Part 2 — New test: Hammer2.1-1.5b vs CPU-resident model as title generator

- Candidates: Hammer2.1-1.5b (GPU-resident, already proven fast) vs.
  `utility` (Qwen3-8B, CPU-resident) — the two real candidates for a
  cheap background titling job per the app's actual roster.
- Reuse the existing title prompts (`title1.txt`, `title2.txt` in the
  scratchpad from Test 3, or regenerate a slightly larger set — 5-8 short
  exchanges) and a fixed rubric: correct length (5-8 words per the
  original ask), no wrapping quotes/formatting cruft, semantically on-point.
- Run both models on the same prompt set, one GPU/CPU job at a time, record
  latency + a pass/fail per rubric item.
- Decide: **whichever is faster AND at least as accurate wins** the title-gen
  role; if one is faster but less accurate, judge on the accuracy bar first
  (titles are user-facing, correctness matters more than shaving ~0.2s).
- Write result into `docs/phase0-measurements.md` as a new small section
  (append to the existing §8 concurrent-tests area or a new short §12 —
  keep it short, this is a small test).

## Part 3 — Trim needle/Cactus sections in `docs/phase0-measurements.md`

Find the needle/Cactus paragraphs (external-research narrative, multiple
rounds of Perplexity findings, HN-report summaries) and compress to:
**2 sentences max** — what failed, why, verdict. Delete the rest. Do not
touch any other section of this file (user confirmed the rest is good).

## Part 4 — Full rewrite of `docs/PHASE0_FINDINGS_SUMMARY.md`

Replace the entire file. New structure, concise, no fluff, no repeated
reasoning already stated elsewhere, no narrating what changed from a prior
version (that's this plan's job, not the doc's):

1. **One verdict table** — every locked roster slot (chat-default, coder,
   reasoner, vision, classifier, embed, dispatcher/Hammer, FunctionGemma
   status, utility placement, vector store) with a single-line reason
   each, pointing to the relevant `phase0-measurements.md` section number
   for detail rather than re-explaining.
2. **Needle/Cactus** — 2 sentences, matching the trimmed
   phase0-measurements.md version exactly (no separate longer version here).
3. **Open action items** — only things not yet done (llama-swap swap
   latency, stale granite4 config entries, any real bug still open).
   Remove closed items entirely rather than marking them "resolved" with
   paragraphs of context — closed means gone from this list.
4. **Future tests / follow-ups worth doing later** (new section, capturing
   the user's own ideas from this session rather than losing them):
   - FunctionGemma-270M real production-traffic side-by-side vs Hammer
     (not a synthetic bench) before considering it as a secondary/fallback
     dispatcher.
   - A debug panel / dev-tool surface for manually triggering and
     comparing dispatcher candidates (Hammer vs FunctionGemma vs whatever)
     against live traffic — flagged as a functionality gap, not yet built.
   - Any other forward-looking item currently buried in prose elsewhere
     (dynamic util-load-on-demand hot-swap test, Hammer title-format
     post-processing cleanup) — move here if not already captured.
5. Keep the harness/hygiene notes (§4/§5 in the old doc) since those are
   genuinely useful process notes, but trim to bullet points only.

## Part 5 — Additional tests worth running (this round or flagged as follow-ups)

Thinking beyond the four fixes above, these close real gaps in the current
roster picture:

- **Multi-slot concurrent-user throughput**: llama-server supports
  `--parallel N` (multiple slots sharing one loaded model). Nothing so far
  tests 2-3 *simultaneous chat-default users* hitting the same server —
  only single-stream tok/s exists. Real number needed before assuming the
  app can serve more than one user/session at once without serializing.
- **Context-depth degradation**: does chat-default/coder/reasoner
  correctness or speed degrade as the conversation fills toward 32k
  (vs. the short single-turn prompts used everywhere so far)? A
  20-30-turn synthetic conversation, then a correctness probe at the end,
  would catch long-context quality collapse or KV-cache-driven slowdown
  that a one-shot prompt never exercises.
- **Sustained-load thermal/throttle check**: all benches so far are short
  bursts. A 10-15 minute continuous-generation run on the 3090 would catch
  clock-throttling degradation (consumer cards commonly lose 10-20% tok/s
  under sustained load) that burst tests can't see — relevant since real
  usage isn't single 256-token bursts.
- **Embedding retrieval quality, not just latency**: embed-B was only
  timed (ms/call), never checked for actual retrieval accuracy
  (recall@k on a small labeled query/passage set). A fast embedder that
  retrieves badly is worse than a slower one that's accurate — this is a
  real gap given Qdrant is the adopted store.
- **Classifier broader accuracy pass**: only 5 hand-picked intents were
  tested. A 20-30 example set spanning ambiguous/edge-case phrasing would
  give a real accuracy number instead of a small spot-check.
- **JSON/structured-output reliability beyond tool-calling**: if the app
  needs plain structured JSON output (not function calls) anywhere, no
  model has been tested for raw JSON-schema adherence — worth a quick
  pass if that's a real usage path.
- **Hammer/FunctionGemma stress test on a harder tool registry**: current
  eval uses the app's real 6-tool set, which per earlier findings is
  "narrow, low-ambiguity" — worth a harder synthetic registry (10-15 tools
  with deliberately overlapping names/params) to see if the current
  dispatcher picks still hold up, since production tool registries tend to
  grow.
- **llama-swap real swap-latency test** (already an open item, restated
  here for completeness): cold-swap time between two roster models via
  the actual `llama-swap` process, not synthetic boot timers.

Not all of these need to happen this round — flag the ones not run as
**Future tests** in the rewritten `PHASE0_FINDINGS_SUMMARY.md` (Part 4,
item 4) rather than letting them fall out of scope silently.

## Verification

- Confirm re-run reasoner/coder_debug JSONL rows show non-empty responses
  for all 13 prompts (7 reasoner + 6 debug) across both reasoner models at
  the new token budget, or explicitly document any remaining truncation.
- Confirm the Hammer-vs-CPU-titler test produces a clear winner with real
  latency+accuracy numbers, not a tie left unresolved.
- Diff-check `PHASE0_FINDINGS_SUMMARY.md` before/after: the new version
  should be meaningfully shorter and contain zero paragraphs duplicated
  from `phase0-measurements.md` — every finding stated once, in one file,
  with the other file just pointing to it by section number.
