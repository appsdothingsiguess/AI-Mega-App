# Classifier Prompt Eval Ledger

Append-only run log. Do not overwrite past rows. Promote a winner only after smoke composite ≥ 85% and full ≥ 80% (or best of N).

## Protocol

1. Pull Ollama tags; pick ≤3 classifier candidates (always include current `router.classifier`).
2. Smoke (`--smoke`) with the prompt under test; record all four field accuracies + composite.
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
|        |      |                |           |        |       |       |      |       |

## Winner

_(none yet)_
