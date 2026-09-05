# Agent Platform Decision and Research Plan

**Date:** 2026-09-05  
**Status:** Decision record

## Decision

Keep AI Mega App as the local AI infrastructure layer, but do not continue treating its custom web application and agent loop as the primary product.

The intended boundary is:

```text
Pi.dev or Hermes
  → capture/compatibility relay
    → AI Mega App :8000
      → llama-swap :8080
        → llama.cpp model servers
```

AI Mega App remains responsible for local model serving, routing where useful, GPU/model lifecycle, and the OpenAI-compatible endpoint. Pi.dev and Hermes are evaluated as interchangeable agent clients.

No codebase merge is planned.

## Why the infrastructure stays

The AI Mega App repository contains valuable, already-researched infrastructure:

- llama.cpp and llama-swap deployment
- GPU placement and model lifecycle management
- OpenAI-compatible API access
- model aliases for normal chat, coding, reasoning, and vision
- local relay support for remote clients
- benchmarking and operational knowledge

The repository's custom chat UI, custom agent orchestration, and compaction system are not required if a mature external agent client can provide those capabilities more reliably.

## Current problem

The important unresolved problem is not basic inference. It is long-session continuity:

1. context grows during coding work;
2. the client compacts or summarizes the conversation;
3. tool calls, tool results, file state, and pending work must remain recoverable;
4. the agent must resume correctly after compaction, restart, session replacement, or fork;
5. the agent should accumulate useful preferences, facts, and procedures over time.

A larger context window alone is not a sufficient solution. It increases the amount of information available, but does not guarantee that the right information survives compaction.

## Pi.dev

Pi.dev is currently the best-researched candidate because it has already been tested against the local model stack. The relay is a critical research instrument, not merely a workaround. It exposes the exact client/backend boundary and can capture:

- prompts and tool schemas;
- cached and uncached context;
- reasoning content;
- compaction requests;
- token counts and timing;
- model selection;
- post-compaction behavior.

The relay should remain in place and become a general compatibility and evaluation harness.

Pi.dev's principal known weakness is compaction/context lifecycle behavior. In particular, delayed callbacks must not retain or write through a stale context after session replacement, reload, or fork. Any compaction result must be associated with the session and context version that produced it, then discarded if that state is no longer current.

## Hermes

Hermes is a serious candidate because memory, skills, and continuity are first-class features in its design. Its public repository describes:

- persistent memory and user profiles;
- FTS5 search across prior sessions;
- procedural memory through reusable skills;
- skills created and improved through experience;
- context compression through `/compress`;
- subagents and MCP integration.

These claims should not be accepted solely from documentation. The source code and tests must be treated as authoritative, especially where documentation may be AI-generated.

Hermes learning should be understood precisely: it is persistent memory, user modeling, and procedural skill accumulation—not training or modifying the local model weights.

Hermes should be tested as a separate client against the same AI Mega App endpoint. It should not be merged into AI Mega App.

## Evaluation design

Pi.dev and Hermes must use the same:

- local model;
- AI Mega App endpoint;
- relay/capture layer;
- coding repository;
- task prompts;
- context limits where possible;
- restart and compaction scenarios.

The decisive test is not first-response quality. It is whether the agent can resume an unfinished coding task after context compaction and session restart without being re-prompted.

### Required test cases

| Test | Measurement |
|---|---|
| Long coding task | Correctness and number of corrective turns |
| Forced compaction | Whether exact working state survives |
| Tool-heavy task | Whether tool calls/results remain coherent |
| Session restart | Whether the task resumes accurately |
| Session fork/reload | Whether stale context is avoided |
| Memory recall | Whether deliberately stored facts return later |
| Skill reuse | Whether a learned procedure improves a later task |
| Error recovery | Whether failed commands and unresolved issues remain visible |
| Context pressure | Time, token use, and failure point |
| Local-model compatibility | Streaming, reasoning content, tool calls, and model switching |

### Working-state preservation rubric

After compaction, the client should preserve:

- current objective;
- files changed;
- files still requiring changes;
- tests run and their results;
- known failures;
- pending commands;
- user decisions;
- unresolved questions;
- exact error messages;
- relevant tool outputs.

A prose summary is useful but should not be the only representation of this state.

## Recommended compaction safety model

Compaction should use a session/context generation check:

```text
begin compaction
  capture session_id and context_version

await summary or compression model

before applying result
  if session_id or context_version changed:
      discard result
  else:
      apply result atomically
```

The compaction record should be durable and auditable. A structured working-state block should be kept separately from general narrative memory.

## Recommendation

1. Keep AI Mega App and llama.cpp/llama-swap unchanged as the infrastructure baseline.
2. Preserve and improve the Pi relay.
3. Test Pi.dev first because it has the strongest existing evidence in this environment.
4. Test Hermes as the memory/learning-oriented alternative.
5. Choose based on recovery quality after compaction, restart, and tool-heavy work.
6. Only build custom memory or compaction code if both clients fail the same clearly defined requirement.

This is an agent-client evaluation problem, not a reason to rewrite the local inference stack.
