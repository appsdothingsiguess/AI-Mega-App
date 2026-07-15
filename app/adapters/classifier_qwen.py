"""Qwen intent classifier adapter backed by Ollama."""

from __future__ import annotations

import json
import logging
import time

import httpx

from app.config import Settings, render_classifier_prompt
from app.protocols import Classifier
from app.types import ClassifierOutput

logger = logging.getLogger("prompter.router")

# Shared by classify() and warmup(). qwen2.5:3b fits in VRAM (num_gpu=999 =
# full offload). num_ctx must exceed the rendered mut12 prompt (~4.4k tokens);
# 4096 truncates the system prompt and the model answers the user instead of
# classifying.
CLASSIFIER_OLLAMA_OPTIONS: dict[str, float | int] = {
    "temperature": 0.0,
    "top_k": 20,
    "top_p": 0.8,
    "repeat_penalty": 1.05,
    "num_predict": 250,
    "num_ctx": 8192,
    "num_gpu": 999,
}


class QwenClassifierAdapter(Classifier):
    """Classify user messages with the configured local Ollama classifier."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def warmup(self) -> None:
        """Pre-load the classifier into Ollama with the same options as classify()."""
        payload = {
            "model": self._ollama_model_name(),
            "prompt": "",
            "stream": False,
            "keep_alive": self.settings.ollama.keep_alive,
            "options": dict(CLASSIFIER_OLLAMA_OPTIONS),
        }
        timeout_s = self.settings.health.classifier_timeout_s or 30.0
        async with httpx.AsyncClient(timeout=max(timeout_s, 60.0)) as client:
            response = await client.post(
                f"{self.settings.ollama.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
        logger.info(
            "Classifier warmed: model=%s num_gpu=%s num_ctx=%s",
            payload["model"],
            CLASSIFIER_OLLAMA_OPTIONS["num_gpu"],
            CLASSIFIER_OLLAMA_OPTIONS["num_ctx"],
        )

    async def classify(self, message: str) -> ClassifierOutput:
        started = time.perf_counter()
        payload = {
            "model": self._ollama_model_name(),
            "system": render_classifier_prompt(
                self.settings.router.classifier_prompt, self.settings.models
            ),
            "prompt": message,
            "stream": False,
            "keep_alive": self.settings.ollama.keep_alive,
            "options": dict(CLASSIFIER_OLLAMA_OPTIONS),
        }

        timeout_s = self.settings.health.classifier_timeout_s or 30.0
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
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
            data = json.loads(self._extract_json(response_text))
        except json.JSONDecodeError:
            return self._fallback()

        if not isinstance(data, dict):
            return self._fallback()

        intent = data.get("intent")
        tools = data.get("tools", [])
        confidence = data.get("confidence", 0.0)
        model = data.get("model")
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
            model=self._validate_model_alias(model),
        )

    def _validate_model_alias(self, model: object) -> str | None:
        if not isinstance(model, str) or not model.strip():
            return None
        model = model.strip()
        if model in self.settings.models.values():
            return model
        logger.warning("Classifier suggested unknown model alias %r; ignoring", model)
        return None

    @staticmethod
    def _extract_json(response_text: str) -> str:
        """Return the first JSON object substring from model output."""
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return text

    def _fallback(self) -> ClassifierOutput:
        return ClassifierOutput(intent="general_chat", tools=[], confidence=0.0)
