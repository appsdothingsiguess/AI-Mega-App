"""Layer-3 grammar-constrained classifier (PLAN.md §4.3, docs/FEATURES.md F5).

Calls the CPU-resident classifier model via LLMClient with
response_format=json_schema so malformed JSON is structurally impossible —
the old build's 4.4k defensive prompt collapses to ~600 tokens here.

THREE LOAD-BEARING PROPERTIES (do not re-derive — Phase 0 §13):
  1. thinking=False on every call → llama-server's per-request reasoning
     suppression. Never a /no_think suffix; it silently fails on some
     checkpoints, leaving content empty and reasoning_content full.
  2. The few-shot examples below target the two observed confusion pairs:
       • live data with no tool named: "stock price", "weather"
       • file-search vs code-writing: "grep", "find files"
  3. max_tokens=1024 — an under-budgeted call returns empty and reads as a
     model failure; it is not one (PLAN.md §4.1, rule 010).

Output schema (frozen 2026-07-23 — the taxonomy that measured 91.76%):
  {"class": <one of 6 labels>, "confidence": <float 0-1>}

`effort` and `needs_tools` are NOT classifier output; the rules layer sets
them as span fields if needed in later phases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from app.config import RoutingClassifierConfig
from app.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase-0 prompt, ported verbatim.  Category definitions and few-shots come
# from scripts/eval_classifier_accuracy.py PROMPT_TEMPLATE; only the output
# contract changes from a bare label to a JSON object.
# ---------------------------------------------------------------------------
CLASSIFIER_PROMPT = (
    "You are a routing classifier for an AI assistant. Classify the user's "
    "message into EXACTLY ONE category. Output a single JSON object with "
    'keys "class" and "confidence" (0.0-1.0, your certainty). '
    "No other text.\n\n"
    "Categories:\n"
    "- chat: informational/explanatory questions or requests for opinions "
    "the assistant can answer directly from its own knowledge (no live "
    "data, no tools, no code writing).\n"
    "- code_task: the user wants code written, refactored, debugged, or "
    "reviewed.\n"
    "- tool_call_needed: the user needs a live/external action -- web "
    "search, fetching a URL, reading/searching local files, running code, "
    "or saving/recalling a memory. Anything requiring real-time or "
    "external data/state, even if phrased casually.\n"
    "- reasoning_task: an explicit multi-step logic/math puzzle or a "
    "request to think step by step / reason through something before "
    "answering, where the deliberation itself is the point.\n"
    "- vision_task: the message references an attached image, screenshot, "
    "photo, or diagram that must be visually interpreted.\n"
    "- chit_chat: a short conversational reply with no informational "
    "request -- acknowledgments, thanks, greetings, filler.\n\n"
    "Examples:\n"
    'Message: "What\'s the difference between a list and a tuple in Python?"\n'
    '{"class":"chat","confidence":0.95}\n\n'
    'Message: "Write a Python function to reverse a linked list."\n'
    '{"class":"code_task","confidence":0.97}\n\n'
    'Message: "Search the web for the latest Node.js LTS version."\n'
    '{"class":"tool_call_needed","confidence":0.95}\n\n'
    'Message: "Remember that I prefer 2-space indentation from now on."\n'
    '{"class":"tool_call_needed","confidence":0.9}\n\n'
    'Message: "What\'s the current stock price of Nvidia?"\n'
    '{"class":"tool_call_needed","confidence":0.9}\n'
    "(real-time/live data the assistant can't know from training -- "
    "tool_call_needed even with no tool named explicitly, NOT chat)\n\n"
    'Message: "Find every file in the app/ directory that imports os."\n'
    '{"class":"tool_call_needed","confidence":0.9}\n'
    "(searching/reading existing files -- tool_call_needed, NOT code_task; "
    "code_task is only for writing/modifying/reviewing code)\n\n"
    'Message: "A farmer has 17 sheep. All but 9 die. How many are left? '
    'Show your reasoning."\n'
    '{"class":"reasoning_task","confidence":0.95}\n\n'
    'Message: "How many objects are in this image?"\n'
    '{"class":"vision_task","confidence":0.98}\n\n'
    'Message: "haha nice, thanks!"\n'
    '{"class":"chit_chat","confidence":0.97}\n\n'
    "Now classify:\n"
    'Message: "{message}"\n'
)

# JSON schema enforced by llama.cpp's grammar-constrained sampler.
# Malformed JSON is structurally impossible with this response_format.
RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "route_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "class": {
                    "type": "string",
                    "enum": [
                        "chat",
                        "chit_chat",
                        "code_task",
                        "tool_call_needed",
                        "reasoning_task",
                        "vision_task",
                    ],
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["class", "confidence"],
            "additionalProperties": False,
        },
    },
}


async def classify(
    text: str,
    *,
    llm_client: LLMClient,
    cfg: RoutingClassifierConfig,
    details: dict | None = None,
) -> tuple[str, float] | None:
    """Call the classifier model and return (class, confidence) or None.

    None means any failure: timeout, HTTP error, malformed response, missing
    content.  The caller (router.py) applies the fallback; this function
    never raises into the call path.

    `details`, when given, is filled in-place with what the classifier was
    actually sent and got back (`prompt`, `raw_response`, `classifier_model`,
    `classifier_ms`, and `classifier_error` on failure) so the route span can
    show it in the Debug view.  `prompt`/`raw_response` are dropped by
    app/debug/trace.py unless `debug.store_prompts` is true.

    thinking=False sends an explicit per-request reasoning=off to llama-swap,
    supplementing the server's --reasoning off flag.  Both are required:
    the server flag is the reliable gate; the per-request flag is a belt.
    """
    # Use str.replace instead of .format() — the few-shot examples contain
    # JSON braces that confuse str.format()'s field parser.
    prompt = CLASSIFIER_PROMPT.replace("{message}", text)
    messages = [{"role": "user", "content": prompt}]

    def record(**fields: object) -> None:
        if details is not None:
            details.update(fields)

    started = time.monotonic()
    record(prompt=prompt, classifier_model=cfg.model)
    try:
        delta = await asyncio.wait_for(
            _single_completion(llm_client, cfg.model, messages),
            timeout=cfg.timeout_s,
        )
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("router.classifier: timeout after %.1fs", cfg.timeout_s)
        record(
            classifier_ms=(time.monotonic() - started) * 1000,
            classifier_error=f"timeout after {cfg.timeout_s}s",
        )
        return None
    except LLMError as exc:
        logger.warning("router.classifier: llm error %s", exc)
        record(
            classifier_ms=(time.monotonic() - started) * 1000,
            classifier_error=f"{exc.kind}: {exc.detail}",
        )
        return None

    record(classifier_ms=(time.monotonic() - started) * 1000, raw_response=delta)

    if not delta:
        logger.warning("router.classifier: empty response content")
        record(classifier_error="empty response content")
        return None

    try:
        obj = json.loads(delta)
        cls = obj["class"]
        conf = float(obj["confidence"])
        return cls, conf
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("router.classifier: parse error %s (content=%r)", exc, delta)
        record(classifier_error=f"parse error: {exc}")
        return None


async def _single_completion(
    llm_client: LLMClient, model: str, messages: list[dict]
) -> str | None:
    """Collect one non-streaming completion and return the content string."""
    content_parts: list[str] = []
    async for delta in llm_client.chat(
        model=model,
        messages=messages,
        response_format=RESPONSE_FORMAT,
        thinking=False,
        max_tokens=1024,
        stream=False,
    ):
        if delta.content:
            content_parts.append(delta.content)
    return "".join(content_parts) if content_parts else None
