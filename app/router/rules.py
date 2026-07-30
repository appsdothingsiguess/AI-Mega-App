"""Layer-2 deterministic routing rules (PLAN.md §4.3, docs/FEATURES.md F5).

Pure, synchronous, no model calls. Attachments force intents first; then
config keyword rules (compiled word-boundary regexes, 2+ word phrases).

match() returns the winning intent string or None, letting the caller fall
through to the classifier (layer 3).
"""

from __future__ import annotations

import logging
import re

from app.config import RoutingConfig

logger = logging.getLogger(__name__)


def _attachment_type(item: object) -> str | None:
    """Extract 'type' from an attachment regardless of whether it is a dict
    or an object with a .type attribute."""
    if isinstance(item, dict):
        return item.get("type")
    return getattr(item, "type", None)


def _compile_rule(keywords: list[str]) -> re.Pattern[str] | None:
    """Build a word-boundary pattern for a 2+ word phrase list entry."""
    # keywords is a list of single multi-word phrases (validated by config)
    phrase = r"\s+".join(re.escape(w) for w in keywords[0].split())
    pattern = rf"\b{phrase}\b"
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        logger.warning("router.rules: skipping bad keyword regex %r: %s", keywords, exc)
        return None


def match(text: str, attachments: list, routing: RoutingConfig) -> str | None:
    """Return the first matching intent or None.

    Order: attachment forcing → keyword rules (config order, first wins).
    """
    # Layer 2a: attachment forcing
    for item in attachments:
        atype = _attachment_type(item)
        if atype and atype in routing.attachments:
            intent = routing.attachments[atype]
            logger.debug("router.rules: attachment %r -> %s", atype, intent)
            return intent

    # Layer 2b: keyword rules
    for rule in routing.rules:
        # Each RoutingRule has keywords: list[str] (each element is a 2+ word phrase)
        for phrase in rule.keywords:
            words = phrase.split()
            pattern_str = r"\s+".join(re.escape(w) for w in words)
            pattern = rf"\b{pattern_str}\b"
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    logger.debug("router.rules: keyword %r -> %s", phrase, rule.intent)
                    return rule.intent
            except re.error as exc:
                logger.warning("router.rules: skipping bad keyword pattern %r: %s", phrase, exc)

    return None
