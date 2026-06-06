"""LM Studio inference abstraction (v1 LLM API + OpenAI-compatible REST fallback)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class LMStudioError(Exception):
    """Base error for LM Studio connectivity or inference failures."""


class LMStudioConnectionError(LMStudioError):
    """Server unreachable."""


class LMStudioModelError(LMStudioError):
    """Requested model missing or not loaded."""


@dataclass
class HealthStatus:
    ok: bool
    mode: str
    base_url: str
    model: str
    model_loaded: bool
    message: str
    available_models: list[str]


@dataclass
class ModelCatalogEntry:
    key: str
    display_name: str
    type: str
    loaded: bool
    vision: bool
    params_string: str | None = None


@dataclass
class ModelLoadResult:
    ok: bool
    model: str
    status: str
    instance_id: str | None = None
    load_time_seconds: float | None = None
    message: str = ""


@dataclass
class TextContentPart:
    type: str = "text"
    content: str = ""


@dataclass
class ImageContentPart:
    type: str = "image"
    data_url: str = ""


ContentPart = TextContentPart | ImageContentPart | str


@dataclass
class ChatMessage:
    role: str
    content: str | list[ContentPart]


def message_has_images(messages: list[ChatMessage]) -> bool:
    for msg in messages:
        if isinstance(msg.content, list):
            if any(
                isinstance(p, ImageContentPart)
                or (isinstance(p, dict) and p.get("type") == "image")
                for p in msg.content
            ):
                return True
    return False


def flatten_message_content(content: str | list[ContentPart]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, TextContentPart):
            parts.append(part.content)
        elif isinstance(part, ImageContentPart):
            parts.append("[image]")
        elif isinstance(part, dict):
            if part.get("type") == "text":
                parts.append(str(part.get("content", "")))
            elif part.get("type") == "image":
                parts.append("[image]")
    return "\n".join(p for p in parts if p)


class LMStudioClient:
    """Adapter for LM Studio native `/api/v1/*` and OpenAI-compatible `/v1/*`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.lmstudio_base_url.rstrip("/")
        self.model = settings.lmstudio_model
        self.mode = settings.lmstudio_mode
        self._client = httpx.Client(timeout=120.0)

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        return self.settings.auth_headers()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            response = self._client.request(
                method,
                url,
                headers=self._headers(),
                json=json,
            )
        except httpx.RequestError as exc:
            raise LMStudioConnectionError(
                f"Cannot reach LM Studio at {self.base_url}: {exc}"
            ) from exc
        return response

    def list_models_catalog(self) -> list[ModelCatalogEntry]:
        """List models from LM Studio v1 API with loaded/vision metadata."""
        if self.mode != "llm":
            return self._list_models_catalog_rest()
        response = self._request("GET", "/api/v1/models")
        if response.status_code != 200:
            raise LMStudioError(
                f"Models endpoint returned {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        entries: list[ModelCatalogEntry] = []
        for m in payload.get("models", []):
            key = str(m.get("key", ""))
            if not key:
                continue
            caps = m.get("capabilities") or {}
            instances = m.get("loaded_instances") or []
            entries.append(
                ModelCatalogEntry(
                    key=key,
                    display_name=str(m.get("display_name") or key),
                    type=str(m.get("type") or "llm"),
                    loaded=bool(instances),
                    vision=bool(caps.get("vision")),
                    params_string=m.get("params_string"),
                )
            )
        return entries

    def _list_models_catalog_rest(self) -> list[ModelCatalogEntry]:
        response = self._request("GET", "/v1/models")
        if response.status_code != 200:
            raise LMStudioError(
                f"OpenAI /v1/models returned {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        entries: list[ModelCatalogEntry] = []
        for m in data.get("data", []):
            mid = str(m.get("id", ""))
            if not mid:
                continue
            entries.append(
                ModelCatalogEntry(
                    key=mid,
                    display_name=mid,
                    type="llm",
                    loaded=True,
                    vision=False,
                )
            )
        return entries

    def load_model(
        self,
        model: str,
        *,
        context_length: int | None = None,
    ) -> ModelLoadResult:
        """Load a model via POST /api/v1/models/load (llm mode only)."""
        if self.mode != "llm":
            raise LMStudioError(
                "Model load via API requires LMSTUDIO_MODE=llm. "
                "Load the model manually in LM Studio when using rest mode."
            )
        body: dict[str, Any] = {"model": model}
        if context_length is not None:
            body["context_length"] = context_length
        response = self._request("POST", "/api/v1/models/load", json=body)
        if response.status_code != 200:
            raise LMStudioModelError(
                f"Load failed ({response.status_code}): {response.text[:500]}"
            )
        data = response.json()
        status = str(data.get("status", "loaded"))
        return ModelLoadResult(
            ok=True,
            model=model,
            status=status,
            instance_id=data.get("instance_id"),
            load_time_seconds=data.get("load_time_seconds"),
            message=f"Model '{model}' {status}.",
        )

    def health_check(self) -> HealthStatus:
        if self.mode == "llm":
            return self._health_llm()
        return self._health_rest()

    def _health_llm(self) -> HealthStatus:
        response = self._request("GET", "/api/v1/models")
        if response.status_code != 200:
            return HealthStatus(
                ok=False,
                mode=self.mode,
                base_url=self.base_url,
                model=self.model,
                model_loaded=False,
                message=f"Models endpoint returned {response.status_code}: {response.text[:200]}",
                available_models=[],
            )

        payload = response.json()
        models = payload.get("models", [])
        keys = [m.get("key", "") for m in models]
        active = self._first_loaded_llm_key(models)
        loaded = active is not None
        ok = loaded
        if loaded:
            message = f"Using loaded model '{active}'."
        else:
            message = "No model loaded in LM Studio — load a model in the LM Studio app."
        return HealthStatus(
            ok=ok,
            mode=self.mode,
            base_url=self.base_url,
            model=active or self.model,
            model_loaded=loaded,
            message=message,
            available_models=keys,
        )

    def _health_rest(self) -> HealthStatus:
        response = self._request("GET", "/v1/models")
        if response.status_code != 200:
            return HealthStatus(
                ok=False,
                mode=self.mode,
                base_url=self.base_url,
                model=self.model,
                model_loaded=False,
                message=f"OpenAI-compatible /v1/models returned {response.status_code}",
                available_models=[],
            )

        data = response.json()
        ids = [m.get("id", "") for m in data.get("data", [])]
        loaded = self._match_model_id(ids, self.model)
        return HealthStatus(
            ok=loaded,
            mode=self.mode,
            base_url=self.base_url,
            model=self.model,
            model_loaded=loaded,
            message=(
                f"Model '{self.model}' found via REST."
                if loaded
                else f"Model '{self.model}' not listed. Available: {', '.join(ids[:8])}"
            ),
            available_models=ids,
        )

    @staticmethod
    def _match_model_id(ids: list[str], target: str) -> bool:
        target_l = target.lower()
        for model_id in ids:
            mid = model_id.lower()
            if mid == target_l or mid.endswith(target_l) or target_l in mid:
                return True
        return False

    @staticmethod
    def _model_loaded_in_catalog(models: list[dict[str, Any]], target: str) -> bool:
        target_l = target.lower()
        for entry in models:
            key = str(entry.get("key", "")).lower()
            if not (key == target_l or key.endswith(target_l) or target_l in key):
                continue
            instances = entry.get("loaded_instances") or []
            if instances:
                return True
        return False

    @staticmethod
    def _first_loaded_llm_key(models: list[dict[str, Any]]) -> str | None:
        """Return the key of the first loaded LLM in LM Studio's catalog."""
        for entry in models:
            if str(entry.get("type", "llm")) != "llm":
                continue
            instances = entry.get("loaded_instances") or []
            if instances:
                key = str(entry.get("key", "")).strip()
                if key:
                    return key
        return None

    def effective_chat_model(self, *, use_vision: bool = False) -> str:
        """Model id sent to LM Studio: loaded model from the app, else configured default."""
        if use_vision and self.settings.lmstudio_vision_model:
            return self.settings.lmstudio_vision_model
        if self.mode == "llm":
            response = self._request("GET", "/api/v1/models")
            if response.status_code == 200:
                models = response.json().get("models", [])
                loaded = self._first_loaded_llm_key(models)
                if loaded:
                    return loaded
                if self._model_loaded_in_catalog(models, self.model):
                    return self.model
        return self.model

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        use_vision: bool = False,
    ) -> str:
        non_system = [m for m in messages if m.role != "system"]
        if len(non_system) > 1:
            return self._chat_rest(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                use_vision=use_vision,
            )
        if self.mode == "llm":
            return self._chat_llm(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                use_vision=use_vision,
            )
        return self._chat_rest(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            use_vision=use_vision,
        )

    def _chat_llm(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        use_vision: bool,
    ) -> str:
        system_parts = [
            flatten_message_content(m.content)
            for m in messages
            if m.role == "system"
        ]
        non_system = [m for m in messages if m.role != "system"]

        body: dict[str, Any] = {
            "model": self.effective_chat_model(use_vision=use_vision),
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens

        if system_parts:
            body["system_prompt"] = "\n\n".join(system_parts)

        if len(non_system) == 1 and non_system[0].role == "user":
            body["input"] = self._build_input_payload(non_system[0].content, use_vision=use_vision)
        else:
            body["input"] = self._format_transcript(non_system, use_vision=use_vision)

        response = self._request("POST", "/api/v1/chat", json=body)
        if response.status_code != 200 and "system_prompt" in body:
            fallback_input = body.get("input", "")
            system_text = body.pop("system_prompt", "")
            if isinstance(fallback_input, str):
                body["input"] = f"SYSTEM:\n{system_text}\n\n{fallback_input}"
            response = self._request("POST", "/api/v1/chat", json=body)
        if response.status_code != 200:
            raise LMStudioError(
                f"LM Studio /api/v1/chat failed ({response.status_code}): {response.text[:500]}"
            )

        data = response.json()
        return self._extract_llm_output(data)

    @staticmethod
    def _build_input_payload(
        content: str | list[ContentPart],
        *,
        use_vision: bool,
    ) -> str | list[dict[str, Any]]:
        if isinstance(content, str):
            return content
        if not use_vision:
            return flatten_message_content(content)
        parts: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, TextContentPart):
                parts.append({"type": "text", "content": part.content})
            elif isinstance(part, ImageContentPart):
                parts.append({"type": "image", "data_url": part.data_url})
            elif isinstance(part, dict):
                parts.append(part)
        return parts if parts else ""

    def _format_transcript(
        self,
        messages: list[ChatMessage],
        *,
        use_vision: bool,
    ) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.role.upper()
            text = flatten_message_content(msg.content)
            if use_vision and isinstance(msg.content, list):
                payload = self._build_input_payload(msg.content, use_vision=True)
                if isinstance(payload, list):
                    text = " ".join(
                        p.get("content", "[image]")
                        if p.get("type") == "text"
                        else "[image]"
                        for p in payload
                    )
            lines.append(f"{role}:\n{text}")
        return "\n\n".join(lines)

    @staticmethod
    def _extract_llm_output(data: dict[str, Any]) -> str:
        output = data.get("output") or []
        parts: list[str] = []
        for item in output:
            if item.get("type") == "message" and item.get("content"):
                parts.append(str(item["content"]))
        if parts:
            return "\n".join(parts).strip()
        if isinstance(data.get("output"), str):
            return str(data["output"]).strip()
        raise LMStudioError(f"Unexpected /api/v1/chat response shape: {data!r}")

    def _chat_rest(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int | None,
        use_vision: bool,
    ) -> str:
        api_messages: list[dict[str, Any]] = []
        for m in messages:
            if use_vision and isinstance(m.content, list):
                content_parts: list[dict[str, Any]] = []
                for part in m.content:
                    if isinstance(part, TextContentPart):
                        content_parts.append({"type": "text", "text": part.content})
                    elif isinstance(part, ImageContentPart):
                        content_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": part.data_url},
                            }
                        )
                api_messages.append({"role": m.role, "content": content_parts})
            else:
                api_messages.append(
                    {
                        "role": m.role,
                        "content": flatten_message_content(m.content),
                    }
                )

        body: dict[str, Any] = {
            "model": self.effective_chat_model(use_vision=use_vision),
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        response = self._request("POST", "/v1/chat/completions", json=body)
        if response.status_code != 200:
            raise LMStudioError(
                f"OpenAI-compatible chat failed ({response.status_code}): {response.text[:500]}"
            )

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LMStudioError(f"Unexpected /v1/chat/completions response: {data!r}") from exc
