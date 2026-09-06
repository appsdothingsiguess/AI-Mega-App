# Pi / GooseDump integration contract

**Status:** current operational contract (2026-09-05)

This document is the shared boundary between the Ubuntu `AI-Mega-App` services
and the Windows Pi/GooseDump client. App architecture stays in this repository;
GooseDump implementation and client tests stay in `Programming/test`.

## Topology

```text
Windows Pi / GooseDump
        |
        | OpenAI-compatible model requests
        v
192.168.0.89:8081  pi-capture-relay
        |
        v
127.0.0.1:8080      llama-swap / llama-server roster

Windows GooseDump memory client
        |
        | authenticated memory API requests
        v
192.168.0.89:8091  pi-memory-service
```

Port `8081` is the model endpoint for normal GooseDump/Pi operation. It routes
to whichever model Pi has selected through the production relay; clients must
not hard-code a model such as Qwen3.6. Port `8091` is storage/search only. It is
not an LLM endpoint and does not perform compaction or claim extraction.

Port `8082` is reserved for the isolated Qwen3.6 diagnostic worker mode. It is
not part of the normal GooseDump compact-summary path.

## Request flow

1. Pi invokes native `ctx.compact()` for `/goose-compact` or threshold
   compaction.
2. Pi sends the compaction request through its active provider/model. In the
   normal installation this is the `locallm` provider at
   `http://192.168.0.89:8081/v1`.
3. Pi replaces the older session range with the generated summary while
   retaining the recent message tail. Future model calls receive both.
4. The GooseDump extension optionally extracts durable claims from the supplied
   content or completed compaction summary using that same active Pi model.
5. Extracted claims are posted to the authenticated memory service at `:8091`.
6. Failed extraction or save attempts are written to the client retry queue;
   the next settled turn retries one job. Queue state is visible through
   `/goose-memory-status` and paused jobs can be retried with
   `/goose-memory-retry`.

## Configuration boundary

- Pi model provider/model selection belongs in the Windows Pi settings.
- Relay ownership, upstream llama-swap configuration, and GPU placement belong
  in this repository's `ops/` and service configuration.
- Memory-service authentication belongs in the client environment file; never
  commit tokens to either repository.
- The client may override the memory URL with `PI_MEMORY_SERVICE_URL`; the
  default is `http://192.168.0.89:8091`.

## Verification

From Windows/Pi:

```text
/goose-compact
/goose-memory-status
```

Success means compaction completes and the status reports the remote memory
counts with `0 pending` and `0 paused` retry jobs. A non-empty retry queue means
the model or memory service path needs investigation; it does not indicate a
compaction failure by itself.

On Ubuntu, inspect the model relay and its upstream independently:

```bash
systemctl --user status pi-capture-relay.service
ss -ltnp | rg ':8081\\b'
curl -sS http://127.0.0.1:8080/v1/models
```

The memory service is authenticated, so an unauthenticated health/API request
may return `401` even while the service is healthy.

## Ownership and source links

- Ubuntu services and deployment: `ops/`, `pi-memory-service/`, and the AI-Mega
  architecture documents in this repository.
- Windows extension source/tests: `Programming/pi-goosedump-remote/`.
- Windows test notes and operational scripts: `Programming/test/`.

When these documents disagree, update this contract after verifying the live
service configuration, then update the project-specific note that was stale.
