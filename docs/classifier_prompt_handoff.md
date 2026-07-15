# Classifier Prompt Eval — Handoff

For another agent: analyze prompts vs failures; propose targeted mutations; validate carefully. Do not commit/push unless asked.

## Goal / status

| Variant | Run | n | Composite | Intent | Tools | Model | Tier | Median latency |
|---------|-----|---|-----------|--------|-------|-------|------|----------------|
| **local mut12 WINNER (3b GPU)** | `20260715T020647Z` | 253 | **96.8%** | 97.6% | 98.0% | 97.2% | 97.2% | 306 ms |
| local mut9 (3b GPU) | `20260715T015756Z` | 253 | 88.5% | 89.3% | 93.3% | 91.3% | 91.7% | 291 ms |
| **local mut7 (1.5b CPU)** | `20260715T010425Z` | 253 | **86.6%** | 88.5% | 90.9% | 88.5% | 89.3% | 1104 ms |
| remote baseline | `20260715T002731Z` | 253 | **80.2%** | 89.3% | 83.0% | 89.3% | 90.9% | 1453 ms |
| local pre-mut (ref) | `20260715T002117Z` | 253 | 77.1% | 88.9% | 80.2% | 88.9% | 90.1% | 1429 ms |

- Local 3b GPU mut12 meets **≥95%** full (num_ctx=4096, num_predict=250, num_gpu=999).
- Local 1.5b CPU mut7 still the CPU baseline (≥85%, not ≥90%).
- Remote best true-253 still **80.2%**; stratified remote ids later stuck ~70–82% — stop spamming remote fulls.
- Early “full” on n=33 (=smoke) overfitted (90–97%). **True dataset = 253 rows.** Smoke/ids ≠ generalization.

## Key paths

| What | Path |
|------|------|
| Eval prompts | `eval/classifier/prompts/local.txt`, `remote.txt` |
| Gold maps | `eval/classifier/gold_map.json`, `gold_map.remote.json` |
| Dataset | `eval/classifier/dataset.jsonl` (253 rows) |
| Smoke id list | `eval/classifier/smoke_ids.json` (3×11 intents) |
| Harness | `scripts/eval_classifier.py` |
| Dataset builder | `scripts/build_classifier_dataset.py` |
| Append-only ledger | `docs/classifier_prompt.md` |
| Winner artifacts | `eval/classifier/runs/20260715T010425Z/{summary,details,prompt}.json|l|txt` |

**Live = mut12 (Wave 4 done):** `DEFAULT_CLASSIFIER_PROMPT` promotes mut12 wording with `{{MODEL:<intent>}}` placeholders + `confidence`. Eval `local.txt` keeps concrete gold aliases. Re-render via `render_classifier_prompt` for live parity with `settings.models`.

## Winning local run (`mut7`)

- Prompt: `eval/classifier/prompts/local.txt` @ **mut7** (ledger).
- Classifier tag: `qwen2.5:1.5b-32k` (`ollama/qwen2.5:1.5b-32k`).
- Composite 219/253 ok; **34 composite fails**.
- Field misses on full set: intent 29, tools 23, model 29.
- Among composite fails: often multi-field (17× all four; 10× intent+model+tier). Tools-only fails: 4 (coding tool over-predict).

## How to reproduce

**Sequential only** — one variant / one process at a time. Dual concurrent Ollama evals contend and inflate latency / flakiness.

```bash
# Stratified / random-30ish via --ids (build comma list from dataset or smoke_ids.json)
python scripts/eval_classifier.py --ids gc-001,ws-001,... --variant local --models qwen2.5:1.5b-32k

# Full 253
python scripts/eval_classifier.py --full --variant local --models qwen2.5:1.5b-32k
python scripts/eval_classifier.py --full --variant remote --models qwen2.5:1.5b-32k
```

- Classifier under test: **`qwen2.5:1.5b-32k`**.
- Gate: smoke/ids ≥85% on ≥2 stratified draws → then **one** full 253.
- Append every run row to `docs/classifier_prompt.md`.

## Failure analysis (local mut7 full `20260715T010425Z`)

### Worst intents (composite fail rate)

| Intent | Fails | Rate |
|--------|------:|-----:|
| file_ops | 7/23 | 30.4% |
| reasoning_medium | 5/23 | 21.7% |
| web_search | 4/23 | 17.4% |
| coding_basic | 4/23 | 17.4% |
| coding_advanced | 4/23 | 17.4% |
| pdf_gen | 3/23 | 13.0% |
| deep_research | 0/23 | 0% |

### Top intent confusions (count)

- web_search → general_chat (4)
- coding_basic → coding_advanced (3)
- file_ops → coding_advanced (3) / coding_basic (3)
- reasoning_medium → reasoning_heavy (3) / general_chat (2)
- pdf_gen / vision → general_chat (2 each)
- bash → coding_advanced (1)

### Tools vs model

- **Intent miss usually drags model** (tier aliases follow wrong intent).
- **Tools-only** (intent+model ok): coding_* over-adds `bash` or full tool suite (`ca-002`, `ca-012`, `ca-023`, `cb-011`).
- Parse/chatty fail: `gc-001` predicted empty model / `parse_ok=false` (rare but fatal to composite).

### Example fails (`id` → expected → predicted)

| id | expected | predicted |
|----|----------|-----------|
| ws-005 | web_search / tool-calling / `[web_search]` | general_chat / qwen3-8b / `[]` |
| ws-010 | web_search / … / `[web_search]` | general_chat / … / `[]` |
| fo-007 | file_ops / … / `[file_ops]` | coding_advanced / coding-heavy / `[]` |
| fo-014 | file_ops / … / `[file_ops]` | coding_basic / coding-light / `[]` |
| cb-004 | coding_basic / coding-light / `[]` | coding_advanced / coding-heavy / `[]` |
| ba-013 | bash / … / `[bash]` | coding_advanced / coding-heavy / `[bash]` |
| rm-003 | reasoning_medium / reasoning-medium / suite | reasoning_heavy / reasoning-heavy / suite |
| rm-009 | reasoning_medium / … / suite | general_chat / qwen3-8b / `[]` |
| ca-012 | coding_advanced / coding-heavy / `[]` | same intent; tools=`[web_search,bash,pdf_gen,file_ops]` |
| pd-013 | pdf_gen / … / `[pdf_gen]` | general_chat / … / `[]` |

Earlier **77.1%** local full (`20260715T002117Z`) was dominated by deep_research tools=full suite and rm→gc/heavy; mut7 fixed DR intent/tools on full 253 but leftovers moved to **file_ops / ws→gc / cb↔ca / rm↔rh**.

## Soft spots / known issues

1. **deep_research tools** historically over-broad (full suite vs gold); mut7 cleaned intent path — watch regressions when editing tools map.
2. **bash ↔ coding / web_search** boundary (CLI vs code vs fetch).
3. **reasoning_medium ↔ reasoning_heavy** (and rm→general_chat on planning prompts).
4. **file_ops ↔ coding_*** when message looks like “search/read codebase”.
5. **Smoke/ids ≠ full** — n=33 and stratified draws overfit; only trust full 253.
6. **Dual-process Ollama contention** — do not run parallel evals; live classify often &lt;1s when idle vs ~1.1–1.5s median under sequential full eval.
7. ~~Live prompt diverge~~ **resolved** — mut12 wording now in `DEFAULT_CLASSIFIER_PROMPT` with `{{MODEL:}}` + confidence.

## What next AI should do

1. Diff `eval/classifier/prompts/local.txt` (and remote if needed) against the failure clusters above — especially fo / ws→gc / cb↔ca / rm↔rh / coding tools over-predict.
2. Propose **small** prompt mutations (tools map, few-shots for hotspot triples, keep 1.5B-friendly length). One concern per mutation.
3. Validate each mutation with **≥2 stratified random-33** (`--ids`) before **one** full `--full` 253.
4. **Do not** spam concurrent evals or many remote fulls.
5. Log every run in `docs/classifier_prompt.md`.
6. ~~Promote mut12~~ **DONE** — live template adapted with `{{MODEL:}}` + confidence; local `settings.json` override synced.
7. Remote: still below stretch; treat 80.2% as baseline unless a stratified≥85% pattern appears before another full.
8. Watch residual mut12 fails (bash/file_ops/ca tools) if further prompt edits are needed; do not regress ca tools=[] or reasoning suite tools.
