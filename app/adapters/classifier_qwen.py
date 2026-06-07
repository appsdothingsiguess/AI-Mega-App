"""Qwen intent classifier adapter backed by Ollama."""

from __future__ import annotations

import json
import logging
import time

import httpx

from app.config import Settings
from app.types import ClassifierOutput

logger = logging.getLogger("prompter.router")

IDENTITY = (
    "You are Prompter X's local routing classifier. "
    "Your only job is to classify the user's latest message for routing.\n\n"
)


class QwenClassifierAdapter:
    """Classify user messages with the configured local Ollama classifier."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def classify(self, message: str) -> ClassifierOutput:
        started = time.perf_counter()
        payload = {
            "model": self._ollama_model_name(),
            "system": IDENTITY + self.settings.router.classifier_prompt,
            "prompt": message,
            "stream": False,
            "keep_alive": self.settings.ollama.keep_alive,
            "options": {
                "temperature": 0.0,
                "top_k": 20,
                "top_p": 0.8,
                "repeat_penalty": 1.05,
                "num_predict": 96,
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.ollama.base_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                response_text = str(response.json().get("response", ""))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Classifier request failed: %s", exc)
            result = self._fallback()
        else:
            result = self._parse_output(response_text)

        latency_ms = (time.perf_counter() - started) * 1000
        payload = {
            "input": message,
            "intent": result.intent,
            "tools": result.tools,
            "confidence": result.confidence,
            "latency_ms": round(latency_ms, 2),
        }
        logger.info("Classifier decision %s", json.dumps(payload, ensure_ascii=False))
        return result

    def _ollama_model_name(self) -> str:
        classifier = self.settings.router.classifier
        if classifier.startswith("ollama/"):
            return classifier.removeprefix("ollama/")
        return classifier

    def _parse_output(self, response_text: str) -> ClassifierOutput:
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            return self._fallback()

        if not isinstance(data, dict):
            return self._fallback()

        intent = data.get("intent")
        tools = data.get("tools", [])
        confidence = data.get("confidence", 0.0)
        if not isinstance(intent, str) or not isinstance(tools, list):
            return self._fallback()
        if not all(isinstance(tool, str) for tool in tools):
            return self._fallback()

        try:
            parsed_confidence = float(confidence)
        except (TypeError, ValueError):
            return self._fallback()

        return ClassifierOutput(
            intent=intent,
            tools=tools,
            confidence=parsed_confidence,
        )

    def _fallback(self) -> ClassifierOutput:
        return ClassifierOutput(intent="general_chat", tools=[], confidence=0.0)
