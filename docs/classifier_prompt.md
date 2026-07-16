# Classifier Prompt Eval Ledger

Append-only run log. Do not overwrite past rows. Promote a winner only after smoke composite ≥ 85% and full ≥ 80% (or best of N).

## Variants (`--variant`)

- `local` (default): `eval/classifier/prompts/local.txt` + `gold_map.json` — all `local/*` tier aliases (`general_chat` → `local/qwen3-8b`).
- `remote`: `eval/classifier/prompts/remote.txt` + `gold_map.remote.json` — only `general_chat` / `coding_advanced` → `remote/deepseek-v4-pro` and `web_search` / `deep_research` → `remote/kimi-k2-6`; every other intent keeps the local tier alias (no remote vision/bash/etc in litellm).

Override with `--prompt-file` / `--gold-map` when needed. Dataset tools are validated against the active gold map; expected `model` / `tier` always come from that map.

## Protocol

1. Pull Ollama tags; pick ≤3 classifier candidates (always include current `router.classifier`).
2. Smoke (`--smoke --variant local|remote`) with the prompt under test; record all four field accuracies + composite.
3. If composite < 85%: mutate Tools map / Models aliases / few-shots → save prompt variant → re-smoke → append row.
4. When smoke composite ≥ 85%: run `--full`; stop at full composite ≥ 80% or best of N.
5. Mark Winner only after Wave 4 promotion into `DEFAULT_CLASSIFIER_PROMPT`.

## Mutation playbook

- **Tools map**: fix set mismatches (order-insensitive); keep reasoning_* tool list exact.
- **Models / tier aliases**: align prompt Models line to gold map (`local/coding-*`, `local/vision-*`, `local/reasoning-*`, `local/tool-calling-medium`).
- **Few-shots**: show correct `{intent,model,tools}` triples for hotspots.
- **Length**: shorten for 1.5B classifiers if JSON parse failures dominate.

## Ledger

| run_id | mode | classifier_tag | composite | intent | tools | model | tier | notes |
|--------|------|----------------|-----------|--------|-------|-------|------|-------|
| 20260714T235507Z | smoke | qwen2.5:1.5b-32k | 87.9% | 97.0% | 87.9% | 100% | 100% | baseline local.txt; fail ba-003→web_search; dr-00x tools=reasoning suite |
| 20260714T235655Z | smoke | qwen2.5:1.5b-32k | 90.9% | 93.9% | 97.0% | 93.9% | 93.9% | mut1: deep_research tools-only + bash/docker few-shots; fail dr-003 tools, rm-002/003→heavy; early-stop |
| 20260714T235839Z | full | qwen2.5:1.5b-32k | 90.9% | 93.9% | 97.0% | 93.9% | 93.9% | winning local.txt; dataset n=33 (=smoke); same fails dr-003 tools, rm→heavy |
| 20260714T235515Z | smoke | qwen2.5:1.5b-32k | 93.9% | 100% | 93.9% | 100% | 100% | variant=remote; baseline remote.txt; fails dr-003 tools over-broad, vi-001 tools=[] |
| 20260714T235655Z | full | qwen2.5:1.5b-32k | 93.9% | 100% | 93.9% | 100% | 100% | variant=remote; baseline prompt; dataset n=33 (=smoke); same soft spots |
| 20260714T235848Z | smoke | qwen2.5:1.5b-32k | 97.0% | 97.0% | 100% | 97.0% | 97.0% | variant=remote; mut1 tools overrides + dr-003 few-shot; fail ba-003→coding_advanced BEST |
| 20260715T000028Z | smoke | qwen2.5:1.5b-32k | 93.9% | 100% | 93.9% | 100% | 100% | variant=remote; mut2 +bash/docker few-shots; regressed dr-002/003 tools |
| 20260715T000200Z | smoke | qwen2.5:1.5b-32k | 93.9% | 100% | 93.9% | 100% | 100% | variant=remote; mut3 restore mut1+docker; still dr tools fail |
| 20260715T000324Z | smoke | qwen2.5:1.5b-32k | 87.9% | 97.0% | 93.9% | 93.9% | 93.9% | variant=remote; mut4 tools-at-end; worse — discarded |
| 20260715T000440Z | full | qwen2.5:1.5b-32k | 97.0% | 97.0% | 100% | 97.0% | 97.0% | variant=remote; winning mut1 prompt; fail ba-003→coding_advanced |
| 20260715T000551Z | smoke | qwen2.5:1.5b-32k | 97.0% | 97.0% | 100% | 97.0% | 97.0% | variant=remote; confirm mut1 after restore |
| 20260715T002117Z | full | qwen2.5:1.5b-32k | 77.1% | 88.9% | 80.2% | 88.9% | 90.1% | variant=local; TRUE n=253; median 1429ms; worst dr 21.7% (tools=suite), rm 47.8% (→gc/heavy), ca tools dirty |
| 20260715T002731Z | full | qwen2.5:1.5b-32k | 80.2% | 89.3% | 83.0% | 89.3% | 90.9% | variant=remote; TRUE n=253 baseline; median 1453ms; same hotspots + ca tools=[] fails |
| 20260715T003358Z | full | qwen2.5:1.5b-32k | 76.3% | 82.6% | 87.4% | 83.4% | 84.6% | variant=local mut1; dr↑91% rm↑83% but bash→ca (15) cb→ca (10); REGRESS keep as learning |
| 20260715T004027Z | ids | qwen2.5:1.5b-32k | 87.9% | 87.9% | 93.9% | 87.9% | 87.9% | variant=local mut2; stratified seed1 n=33; fail ba→ca, fo→cb, rm→rh |
| 20260715T004122Z | ids | qwen2.5:1.5b-32k | 78.8% | 87.9% | 81.8% | 87.9% | 93.9% | variant=local mut2; stratified seed2 n=33; fail ca tools, fo→vision/gc, ba tools=curl, cb→ca |
| 20260715T004229Z | ids | qwen2.5:1.5b-32k | 90.9% | 90.9% | 97.0% | 90.9% | 90.9% | variant=local mut3; seed1 n=33 |
| 20260715T004322Z | ids | qwen2.5:1.5b-32k | 93.9% | 93.9% | 100% | 93.9% | 93.9% | variant=local mut3; seed2 n=33 BEST-ish |
| 20260715T004410Z | ids | qwen2.5:1.5b-32k | 78.8% | 84.8% | 93.9% | 84.8% | 87.9% | variant=local mut3; seed3 n=33; gc advice→ca/dr, ba git→ca, rh plan→dr |
| 20260715T004525Z | ids | qwen2.5:1.5b-32k | 75.8% | 87.9% | 84.8% | 87.9% | 87.9% | variant=local mut4 REGRESS seed3 — discarded |
| 20260715T004618Z | ids | qwen2.5:1.5b-32k | 81.8% | 87.9% | 90.9% | 87.9% | 90.9% | variant=local mut4 REGRESS seed4 — discarded |
| 20260715T004708Z | ids | qwen2.5:1.5b-32k | 72.7% | 84.8% | 87.9% | 84.8% | 84.8% | variant=local mut4 REGRESS seed1 — discarded |
| 20260715T004816Z | ids | qwen2.5:1.5b-32k | 90.9% | 90.9% | 97.0% | 90.9% | 90.9% | variant=local mut5 (=mut3+tiny); seed1 ready |
| 20260715T004903Z | ids | qwen2.5:1.5b-32k | 100% | 100% | 100% | 100% | 100% | variant=local mut5; seed2 |
| 20260715T004945Z | ids | qwen2.5:1.5b-32k | 87.9% | 93.9% | 93.9% | 90.9% | 93.9% | variant=local mut5; seed3 |
| 20260715T005031Z | ids | qwen2.5:1.5b-32k | 90.9% | 93.9% | 93.9% | 97.0% | 97.0% | variant=local mut5; seed5; 4/4 draws≥85% → full |
| 20260715T005120Z | full | qwen2.5:1.5b-32k | 83.0% | 86.6% | 88.9% | 86.6% | 87.0% | variant=local mut5 TRUE n=253; median 1080ms; miss 85%; worst fo/ws/cb |
| 20260715T005716Z | ids | qwen2.5:1.5b-32k | 75.8% | 78.8% | 81.8% | 75.8% | 75.8% | variant=local mut6 REGRESS — discarded |
| 20260715T010040Z | ids | qwen2.5:1.5b-32k | 84.8% | 84.8% | 87.9% | 84.8% | 84.8% | variant=local mut7 seed1 |
| 20260715T010129Z | ids | qwen2.5:1.5b-32k | 97.0% | 97.0% | 97.0% | 97.0% | 100% | variant=local mut7 seed2 |
| 20260715T010209Z | ids | qwen2.5:1.5b-32k | 87.9% | 87.9% | 87.9% | 87.9% | 87.9% | variant=local mut7 seed8; 2 draws≥85% |
| 20260715T010425Z | full | qwen2.5:1.5b-32k | 86.6% | 88.5% | 90.9% | 88.5% | 89.3% | variant=local mut7 TRUE n=253 WINNER; median 1104ms; ≥85% met (not ≥90%) |
| 20260715T010945Z | ids | qwen2.5:1.5b-32k | 72.7% | 84.8% | 81.8% | 84.8% | 87.9% | variant=remote baseline stratified seed1; stop iterating |
| 20260715T011037Z | ids | qwen2.5:1.5b-32k | 81.8% | 87.9% | 84.8% | 90.9% | 90.9% | variant=remote baseline seed2 |
| 20260715T011128Z | ids | qwen2.5:1.5b-32k | 72.7% | 87.9% | 72.7% | 87.9% | 87.9% | variant=remote baseline seed8 |
| 20260715T011240Z | ids | qwen2.5:1.5b-32k | 81.8% | 90.9% | 87.9% | 90.9% | 90.9% | variant=remote mut1 seed1; still <85% |
| 20260715T011329Z | ids | qwen2.5:1.5b-32k | 78.8% | 87.9% | 84.8% | 87.9% | 90.9% | variant=remote mut1 seed2 |
| 20260715T011415Z | ids | qwen2.5:1.5b-32k | 69.7% | 78.8% | 75.8% | 78.8% | 78.8% | variant=remote mut1 seed8 REGRESS; STOP — no more remote tonight |
| 20260715T015040Z | ids | qwen2.5:3b | 80.0% | 90.0% | 86.7% | 90.0% | 90.0% | GPU VRAM mut7 local random-30 set1; median 287ms |
| 20260715T015053Z | ids | qwen2.5:3b | 83.3% | 96.7% | 86.7% | 96.7% | 96.7% | GPU VRAM mut7 local random-30 set2; median 294ms |
| 20260715T015105Z | ids | qwen2.5:3b | 90.0% | 93.3% | 93.3% | 93.3% | 93.3% | GPU VRAM mut7 local random-30 set3; median 291ms; pooled 76/90=84.4% |
| 20260715T015116Z | ids | granite3.3:2b | 66.7% | 86.7% | 70.0% | 90.0% | 90.0% | GPU VRAM mut7 local random-30 set1; median 185ms |
| 20260715T015132Z | ids | granite3.3:2b | 76.7% | 96.7% | 80.0% | 96.7% | 96.7% | GPU VRAM mut7 local random-30 set2; median 193ms |
| 20260715T015140Z | ids | granite3.3:2b | 90.0% | 93.3% | 90.0% | 93.3% | 93.3% | GPU VRAM mut7 local random-30 set3; median 192ms; pooled 70/90=77.8% |
| 20260715T015535Z | ids | qwen2.5:3b | 93.3% | 100% | 93.3% | 100% | 100% | GPU num_ctx=4096 num_predict=250 mut8 set1; ca tools fixed; rm tools=web_search only |
| 20260715T015548Z | ids | qwen2.5:3b | 83.3% | 93.3% | 86.7% | 93.3% | 93.3% | GPU mut8 set2; rm/rh tools shorten; ba-016→ca |
| 20260715T015600Z | ids | qwen2.5:3b | 90.0% | 96.7% | 93.3% | 96.7% | 96.7% | GPU mut8 set3; pooled mut8 80/90=88.9% |
| 20260715T015627Z | ids | qwen2.5:3b | 100% | 100% | 100% | 100% | 100% | GPU mut9 set1; reasoning tools suite wording |
| 20260715T015639Z | ids | qwen2.5:3b | 90.0% | 90.0% | 93.3% | 96.7% | 96.7% | GPU mut9 set2; fail ba-016, rh→dr |
| 20260715T015651Z | ids | qwen2.5:3b | 90.0% | 93.3% | 93.3% | 93.3% | 93.3% | GPU mut9 set3; pooled mut9 84/90=93.3%; STOP — 3/3 draws≥85% |
| 20260715T015756Z | full | qwen2.5:3b | 88.5% | 89.3% | 93.3% | 91.3% | 91.7% | GPU mut9 TRUE n=253; median 291ms; ≥85% met; worst bash→coding/file_ops (9), rh→rm/dr (7), ca tools/intent (5) |
| 20260715T020209Z | ids | qwen2.5:3b | 75.9% | 86.2% | 82.8% | 89.7% | 89.7% | GPU mut10 on mut9's 29 fails; fixed 22/29 |
| 20260715T020256Z | ids | qwen2.5:3b | 86.2% | 93.1% | 89.7% | 93.1% | 93.1% | GPU mut11 HARD-RULES on same 29 fails |
| 20260715T020307Z | full | qwen2.5:3b | 88.5% | 90.5% | 92.9% | 91.3% | 91.3% | GPU mut11 TRUE n=253; flat vs mut9; new gc/cb/fo regressions — discard |
| 20260715T020452Z | full | qwen2.5:3b | 86.2% | 94.5% | 88.5% | 95.3% | 95.3% | GPU mut10 TRUE n=253 REGRESS — ca tools stuffing (21); discarded |
| 20260715T020647Z | full | qwen2.5:3b | 96.8% | 97.6% | 98.0% | 97.2% | 97.2% | GPU mut12 (=mut9+bash/rh/fo/vision patches) TRUE n=253 WIN ≥95%; median 306ms; 8 fails left |

## Winner

- **Local (1.5b CPU epoch):** mut7 — full **86.6%** on true 253 (`20260715T010425Z`, qwen2.5:1.5b-32k).
- **Local (3b GPU mut12):** full **96.8%** on true 253 (`20260715T020647Z`); prompt `eval/classifier/prompts/local.txt`. num_ctx=4096, num_predict=250, num_gpu=999. ≥95% met. Residual 8: ca-014 tools, ca-018→rm, ba-005/016/019, fo-008/014/021.
- **Remote:** best true-253 full still **80.2%** (`20260715T002731Z`).
- Discarded: mut10/mut11 (88.5%/86.2% — ca tools or gc regressions).

## Wave 4 promotion

- **Promoted:** mut12 local wording → `DEFAULT_CLASSIFIER_PROMPT` in `app/config.py` (and local `settings.json` `router.classifier_prompt`).
- **Adaptation:** concrete gold aliases → `{{MODEL:<intent>}}` placeholders; live JSON keeps `confidence` (fourth key) so router/adapter stay compatible. Eval `local.txt` remains the concrete-alias mut12 artifact.

## 2026-07-16 re-verification (post token-budget-fix pull)

| Run | Type | Model | Composite | Intent | Tools | Model | Tier | Median latency | Note |
|---|---|---|---|---|---|---|---|---|---|
| `20260716T054907Z` | full | qwen2.5:1.5b-32k | 69.6% | 81.8% | 81.8% | 80.2% | 82.2% | 287 ms | `local.txt` had drifted to stale pre-mut12 text; misleading low score, harness artifact not a real regression |
| `20260716T055226Z` | full | qwen2.5:1.5b-32k | 52.6% | 81.8% | 59.7% | 81.8% | 83.0% | 324 ms | `local.txt` re-synced to live mut12, but tested against wrong model (1.5b was never mut12's target; production moved to 3b) — tools compliance (reasoning suite) collapsed |
| `20260716T055503Z` | full | qwen2.5:3b | **96.0%** | 96.4% | 96.8% | 97.2% | 97.6% | 324 ms | Correct model (matches `settings.json`/`app/config.py:376` production default) + re-synced `local.txt` — reproduces `20260715T020647Z`'s 96.8% winner within noise. Confirms no real regression; root cause was `local.txt` staleness compounded by testing against the wrong model. |
