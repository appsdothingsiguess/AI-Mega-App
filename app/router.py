"""Hybrid intent router with keyword rules and classifier fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from app.config import Settings
from app.protocols import Classifier
from app.types import ClassifierOutput, RouteResult, RouteSource

logger = logging.getLogger("prompter.router")


@dataclass(frozen=True)
class _CompiledRule:
    pattern: str
    intent: str
    tools: list[str]


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


class HybridRouter:
    """Route messages through config keyword rules before classifier fallback."""

    def __init__(self, settings: Settings, classifier: Classifier) -> None:
        self.settings = settings
        self.classifier = classifier
        self._rules = self._load_rules()

    def _load_rules(self) -> list[_CompiledRule]:
        if not self.settings.router.rules_enabled:
            return []

        compiled: list[_CompiledRule] = []
        for rule in self.settings.router.rules:
            for raw_pattern in rule.patterns:
                pattern = _normalize_text(raw_pattern)
                if not pattern:
                    continue
                if len(pattern.split()) == 1:
                    logger.warning("Ignoring single-word routing pattern: %s", raw_pattern)
                    continue
                compiled.append(
                    _CompiledRule(
                        pattern=pattern,
                        intent=rule.intent,
                        tools=list(rule.tools),
                    )
                )
        return compiled

    async def route(self, message: str) -> RouteResult:
        normalized_message = _normalize_text(message)

        for rule in self._rules:
            if re.search(r"\b" + re.escape(rule.pattern) + r"\b", normalized_message):
                result = RouteResult(
                    intent=rule.intent,
                    tools=list(rule.tools),
                    confidence=1.0,
                    source=RouteSource.KEYWORD,
                )
                self._log_decision(message, "keyword", result)
                return result

        timeout_s = self.settings.health.classifier_timeout_s or 3.0
        try:
            output = await asyncio.wait_for(
                self.classifier.classify(message),
                timeout=timeout_s,
            )
        except TimeoutError:
            logger.warning(
                "Classifier timed out after %ss; falling back to general_chat",
                timeout_s,
            )
            output = ClassifierOutput(intent="general_chat", tools=[], confidence=0.0)

        result = RouteResult(
            intent=output.intent,
            tools=list(output.tools),
            confidence=output.confidence,
            source=RouteSource.CLASSIFIER,
        )
        self._log_decision(message, "classifier", result)
        return result

    def resolve_model(self, intent: str) -> str:
        default_model = self.settings.models.get("general_chat")
        model = self.settings.models.get(intent, default_model)
        if model is not None:
            return model

        default_attr = getattr(self.settings.models, "general_chat")
        return getattr(self.settings.models, intent, default_attr)

    def _log_decision(self, message: str, layer: str, result: RouteResult) -> None:
        payload = {
            "input": message,
            "matched_layer": layer,
            "intent": result.intent,
            "tools": result.tools,
            "model": self.resolve_model(result.intent),
            "source": result.source.value,
            "confidence": result.confidence,
        }
        logger.info("Routing decision %s", json.dumps(payload, ensure_ascii=False))
