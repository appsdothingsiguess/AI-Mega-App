# Summary coverage diagnostics audit

## Scope

This audit compares the two disposable quality-test worktrees against their
common base, `8452b1c` (`feat: add coder-alt roster and deployment test
script`), using the supplied Summary Coverage Trust Diagnostics prompt.

## Candidate comparison

### `test/chat-default` (`d6ff2ab`)

This branch extracted the existing trust decision into a typed
`coverage_verdict()` and made `trusted_covered_count()` delegate to it. It
added nine focused helper tests and passed them.

It is incomplete for the requested feature:

- it does not change `summary_status.py` or the `/api/debug/summary-status`
  payload;
- it does not change the TypeScript type or Debug-panel rendering;
- it incorrectly reports a committed summary with no summary span as
  `failed_summary` instead of `missing_metadata`;
- it does not test the API payload or frontend consumption.

### `test/coder-alt` (`bea7974`)

This branch does not implement the requested feature. Its commit is an
unrelated background-queue fix that holds the summary in-flight marker across
retry attempts. Its 12 tests pass, but it contains no coverage verdict, API,
TypeScript, or Debug-panel changes to audit for this prompt.

## Implemented result

The complete implementation follows the intended boundary:

`coverage_verdict` → `summary_status` → `/api/debug/summary-status` →
`web/src/types.ts` → `web/src/views/debug.ts`.

The verdict is a frozen typed dataclass with `trusted`, nullable
`covered_count`, and a stable literal reason. It preserves newest-summary
authority and existing prefix/summary fingerprint checks. Missing spans,
malformed JSON, missing fields, wrong-type fields, and malformed error fields
are conservative `missing_metadata`; a nonempty string error is
`failed_summary`; invalid integer ranges and both fingerprint failures have
their dedicated reasons.

The legacy count helper is a compatibility wrapper over the verdict. The
status API keeps its existing top-level fields and adds exactly:

```json
"coverage": {
  "trusted": true,
  "covered_message_count": 2,
  "reason": "ok"
}
```

When untrusted, the nested count is `null` while the legacy top-level count
remains `0`. The Debug panel renders trusted coverage or a human-readable,
HTML-escaped reason. No generated `web/js` file, schema, SSE vocabulary,
dependency, or config file was changed for this feature.

## Files changed for this feature

- `app/background/summary_coverage.py`
- `app/background/summary_status.py`
- `web/src/types.ts`
- `web/src/views/debug.ts`
- `tests/test_summary_coverage_verdict.py`
- `tests/test_background_summary_status.py`
- `tests/test_debug_trace.py`
- `docs/summary_coverage_audit.md`

## Verification

- Candidate `chat-default` helper tests: **9 passed**.
- Candidate `coder-alt` unrelated queue/summary tests: **12 passed**.
- Focused implementation tests (`summary_coverage_verdict`,
  `background_summary_status`, `debug_trace`): **25 passed**, one existing
  FastAPI/httpx deprecation warning.
- `npx tsc --noEmit`: **passed**.
- Full pytest was also run. Its initial run was invalid because the requested
  basetemp already existed; the fresh corrected run reached **222 passed, 1
  failed**. The remaining failure is the pre-existing
  `tests/test_swapgen.py::test_golden`
  mismatch caused by unrelated uncommitted config changes (`--reasoning on`
  and other current roster/performance settings), not by this feature.

There is no separate frontend test harness in this repository, so frontend
behavior is covered by the strict TypeScript check and the API contract tests.
