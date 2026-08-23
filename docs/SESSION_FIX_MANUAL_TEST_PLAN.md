# Session Fixes — Manual Validation Plan

This is a human-run, live-box regression checklist for fixes merged in this session. It intentionally does not use pytest. Run destructive scenarios on staging.

## Setup and evidence

1. Record `git rev-parse --short HEAD`; expected revision is `55d184b` or a descendant. Confirm the session-fix repair is included with `git merge-base --is-ancestor ae5e65e HEAD` (exit status `0`).
2. Open the chat UI and `#/debug` in separate tabs.
3. Watch the service with `sudo journalctl -fu ai-mega-app`.
4. For every case record time, chat ID, trace ID, selected model, result, and a Debug-waterfall screenshot.
5. Treat unexpected results as release blockers except the explicitly marked Phase 3/deployment items.

## 1. Compaction preserves every uncovered message

**Covers:** summary coverage integrity and removal of recent-tail truncation.

1. Create a `chat-default` chat and send 30–40 numbered facts, one per user turn. Put a unique sentinel in the middle: `FACT 31: cobalt-orchid`.
2. Wait for a summary span to finish; note its covered-message count in Debug.
3. Ask: “What is FACT 31? Reply only with its value.”
4. Inspect that request's `llm_request` span.

**Pass:** reply is `cobalt-orchid`; the prompt contains all raw history, or a summary plus every raw message after its coverage point in order. There is no gap.  
**Fail:** a missing range, tail-only history, or a lost sentinel.

## 2. Oversized input is refused safely and recovered by the queue

**Covers:** giant-message handling and removal of unsafe inline compaction.

1. Use a disposable `coder-small` chat.
2. Paste a request much larger than available context and finish with `FINAL-MARKER: violet-otter`.
3. Submit once; do not retry manually.
4. Inspect the terminal event and Debug trace, then wait for summary/recovery status to settle.
5. Ask a short follow-up about `FINAL-MARKER`.

**Pass:** exactly one terminal context error; no chat-model request after safe-fit refusal; recovery is queue-owned; newest user message remains in history.  
**Fail:** a truncated success, duplicate terminal events, missing newest message, or an untracked long-running summary request.

## 3. Invalid/poisoned summaries are never accepted as truth

**Staging only. Covers:** summary SHA/count validation and failure handling.

1. Create a summarized chat as in section 1 and back up its database.
2. Note the summary span's coverage metadata.
3. Stop the service, replace stored summary text with `I cannot summarize this conversation.` without changing its metadata, then start service.
4. Ask for the sentinel fact and restore the backup afterward.

**Pass:** altered summary is rejected; raw history is used or request safely refuses if it cannot fit.  
**Fail:** refusal supplied as trusted summary or history disappears.

## 4. Periodic warmup leaves GPU0 cache alone

**Covers:** GPU0 exclusion and resident warmup identity deduplication.

1. Send the same medium prompt twice to a new `chat-default` chat; record prompt/cache timings in Debug.
2. Leave service idle through a 300-second warmup interval while watching service log.
3. Confirm only CPU/GPU1 residents warm (`utility`, `utility-gpu`, `embed`, `classifier`, `dispatcher`, as configured), not GPU0 swap aliases.
4. Send same prompt a third time.

**Pass:** no generic periodic GPU0 warmup request and normal cache reuse on third request.  
**Fail:** periodic `chat-default`/GPU0 ping or cache reset caused by it.

## 5. Invalid and disabled aliases cannot persist

**Covers:** API validation and model-picker filtering.

1. Confirm model picker lists only enabled aliases.
2. Using browser DevTools request editing or a manual HTTP client, set a chat model to `does-not-exist`, then to a known disabled alias.
3. Send a per-turn message with each invalid alias.
4. Reload chat and inspect saved override.

**Pass:** every attempt returns HTTP 422 before turn persistence; override is unchanged and no llama-swap error is reached.  
**Fail:** invalid alias is displayed, saved, or forwarded to inference.

## 6. Attachments fail safely until Phase 3

**Covers:** removal of attachment black hole. This is not attachment feature acceptance.

1. Send a message with a non-empty `attachments` field using a manual HTTP request or browser request editor.
2. Reload history.

**Pass today:** HTTP 422 says attachments are unsupported until Phase 3, with no message/attachment reference persisted.  
**Note:** actual upload, type sniffing, extraction, context injection, and vision routing are Phase 3 work in `PLAN.md` §4.9 and the `p3/attachments` prompt in `docs/PHASE_PROMPTS.md`.

## 7. GPU0 rewarm is owned, coalesced, and correctly gated

**Covers:** rewarm wiring, same-slot exclusion, delayed-token exclusion, shutdown ownership. Use staging with a short rewarm interval.

1. Complete a substantive response with a non-default GPU0 alias such as `coder`.
2. Wait past configured rewarm interval; observe log/status.
3. Repeat with `reasoner` (same slot as chat-default) and a CPU/GPU1 alias.
4. Create a deliberately slow first token, then restart while a rewarm is pending.

**Pass:** one qualifying non-default GPU0 response rewarms `chat-default`; same-slot, CPU/GPU1, and slow-first-token cases do not. Restart cancels/awaits task cleanly.  
**Fail:** duplicate/same-slot rewarms, rewarm after delayed first token, or dangling task on shutdown.

## 8. Summary jobs do not race and survive restart safely

**Covers:** per-chat in-flight guard and queue-owned recovery/retry.

1. Create a chat near summary threshold.
2. Send two short turns rapidly to request summary work twice.
3. Inspect summary status/spans in Debug while queue runs.
4. On staging, restart during an intentionally slow summary and inspect chat after restart.

**Pass:** no more than one in-flight summary per chat; coverage advances only after complete summary; retry/recovery is queue-owned.  
**Fail:** competing summary writes, advanced coverage after failure, or orphan post-shutdown request.

## 9. Loading telemetry needs actual swap evidence

**Covers:** no false `model_loading`/`swap_wait` during warm prefill.

1. Keep `chat-default` loaded and send a large but fitting prompt that makes first token slow.
2. Inspect Debug trace and UI event sequence.
3. Switch from loaded GPU0 alias to a different unloaded GPU0 alias and send short request.

**Pass:** warm slow prefill has no loading event or `swap_wait` span; verified cold swap emits them only when llama-swap reports target unloaded.  
**Fail:** slow prefill labelled a swap or confirmed cold swap hidden.

## 10. GPU apply reports cold residents honestly

**Covers:** truthful post-apply resident verification. Stage failure simulation only.

1. Apply valid GPU configuration and confirm every required resident is loaded in inventory/status.
2. Make one resident unavailable in staging and apply again.

**Pass:** success only after all residents warm; unavailable resident returns error (normally HTTP 503), never `{ "ok": true }`.  
**Fail:** successful apply while required resident is cold.

## 11. Settings overlays are sparse and references are validated

**Covers:** sparse per-model overlay persistence and cross-field validation.

1. Back up `settings.local.yaml`.
2. Change one permitted field for one model in Settings, such as TTL.
3. Inspect `settings.local.yaml`.
4. Attempt routing/default/background edits referencing unknown and disabled aliases; restore backup after production test.

**Pass:** overlay contains only that model patch; unrelated base models remain visible; invalid references report precise field path and do not change effective configuration.  
**Fail:** full roster serialized, invalid alias validates, or rejected write partly takes effect.

## 12. Concurrent DB work, client shutdown, and Debug limits

**Covers:** serialized SQLite executor access, app-owned client close, trace-limit bounds.

1. Open two browser sessions and stream messages in separate chats while summary job runs. Reload both histories after completion.
2. Restart service while clients/background work are active; inspect logs.
3. In Debug, request trace limits `-1`, `0`, `1`, and very large value through request editor.

**Pass:** no SQLite threading/transaction errors or duplicate/lost messages; shutdown is clean; trace limits are finite and clamped.  
**Fail:** database errors, transport-close warnings, missing/duplicate rows, or unbounded trace response.

## 13. Deployment exposure control — still operational, not code-fixed

Service intentionally binds to `0.0.0.0` and has no authentication contract.

1. From outside trusted LAN, test reachability to port 8000.
2. Confirm firewall/router denies it while localhost and intended LAN client retain access.
3. Record enforcing ACL/firewall rule.

**Pass for current deployment:** untrusted networks cannot reach chat, Debug, or GPU apply at all.  
**Fail/blocker:** untrusted client can reach service. Apply network controls immediately; application auth needs separate design decision.

## Sign-off

Release is manually validated only when all applicable sections pass and staging-only failure simulations are recorded. Phase 3 attachment delivery is a separate future acceptance plan; do not bypass today's safe rejection.
