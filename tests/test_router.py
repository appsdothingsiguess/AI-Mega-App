"""Tests for app/router.py."""

from __future__ import annotations

import asyncio

import pytest

from app.config import HealthSettings, RouterSettings, RoutingRule, Settings
from app.router import HybridRouter
from app.types import ClassifierOutput, RouteSource


class FakeClassifier:
    def __init__(self, output: ClassifierOutput, delay_s: float = 0.0) -> None:
        self.output = output
        self.delay_s = delay_s
        self.calls: list[str] = []

    async def classify(self, message: str) -> ClassifierOutput:
        self.calls.append(message)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.output


def _settings(
    *,
    rules: list[RoutingRule],
    rules_enabled: bool = True,
    timeout_s: float = 3.0,
) -> Settings:
    return Settings(
        projects_dir="./projects",
        data_dir="./data",
        router=RouterSettings(rules=rules, rules_enabled=rules_enabled),
        health=HealthSettings(classifier_timeout_s=timeout_s),
    )


@pytest.mark.asyncio
async def test_keyword_hit_returns_keyword_route_without_classifier() -> None:
    settings = _settings(
        rules=[
            RoutingRule(
                patterns=["weather today"],
                intent="web_search",
                tools=["web_search"],
            )
        ]
    )
    classifier = FakeClassifier(
        ClassifierOutput(intent="general_chat", tools=[], confidence=0.5)
    )
    router = HybridRouter(settings, classifier)

    result = await router.route("Can you tell me the WEATHER   TODAY?")

    assert result.intent == "web_search"
    assert result.tools == ["web_search"]
    assert result.confidence == 1.0
    assert result.source is RouteSource.KEYWORD
    assert classifier.calls == []


@pytest.mark.asyncio
async def test_keyword_uses_word_boundaries_before_classifier_fallback() -> None:
    settings = _settings(
        rules=[
            RoutingRule(
                patterns=["make a pdf"],
                intent="pdf_gen",
                tools=["pdf_gen"],
            )
        ]
    )
    classifier = FakeClassifier(
        ClassifierOutput(intent="general_chat", tools=[], confidence=0.7)
    )
    router = HybridRouter(settings, classifier)

    result = await router.route("Please make a pdfx name for this")

    assert result.intent == "general_chat"
    assert result.source is RouteSource.CLASSIFIER
    assert classifier.calls == ["Please make a pdfx name for this"]


@pytest.mark.asyncio
async def test_keyword_miss_uses_classifier_output() -> None:
    settings = _settings(rules=[])
    classifier = FakeClassifier(
        ClassifierOutput(
            intent="coding_advanced",
            tools=[],
            confidence=0.82,
        )
    )
    router = HybridRouter(settings, classifier)

    result = await router.route("Fix this failing test")

    assert result.intent == "coding_advanced"
    assert result.tools == []
    assert result.confidence == 0.82
    assert result.source is RouteSource.CLASSIFIER


@pytest.mark.asyncio
async def test_single_word_patterns_are_rejected() -> None:
    settings = _settings(
        rules=[
            RoutingRule(
                patterns=["weather", "weather today"],
                intent="web_search",
                tools=["web_search"],
            )
        ]
    )
    classifier = FakeClassifier(
        ClassifierOutput(intent="general_chat", tools=[], confidence=0.4)
    )
    router = HybridRouter(settings, classifier)

    result = await router.route("weather")

    assert result.intent == "general_chat"
    assert result.source is RouteSource.CLASSIFIER
    assert classifier.calls == ["weather"]


@pytest.mark.asyncio
async def test_classifier_timeout_falls_back_to_general_chat() -> None:
    settings = _settings(rules=[], timeout_s=0.01)
    classifier = FakeClassifier(
        ClassifierOutput(intent="web_search", tools=["web_search"], confidence=0.9),
        delay_s=1.0,
    )
    router = HybridRouter(settings, classifier)

    result = await router.route("Need a route")

    assert result.intent == "general_chat"
    assert result.tools == []
    assert result.confidence == 0.0
    assert result.source is RouteSource.CLASSIFIER


def test_resolve_model_covers_all_intents() -> None:
    settings = _settings(rules=[])
    router = HybridRouter(
        settings,
        FakeClassifier(ClassifierOutput(intent="general_chat", tools=[], confidence=0.0)),
    )

    expected = {
        "general_chat": "remote/deepseek-v4-pro",
        "web_search": "remote/kimi-k2-6",
        "deep_research": "remote/kimi-k2-6",
        "coding_basic": "local/qwen2.5-coder-7b",
        "coding_advanced": "remote/deepseek-v4-pro",
        "bash": "local/qwen3-8b",
        "pdf_gen": "local/qwen3-8b",
        "file_ops": "local/qwen3-8b",
        "vision": "local/qwen2.5-vl-3b",
    }

    assert {intent: router.resolve_model(intent) for intent in expected} == expected
    assert router.resolve_model("unknown") == "remote/deepseek-v4-pro"


@pytest.mark.asyncio
async def test_route_source_is_enum_value() -> None:
    settings = _settings(rules=[])
    router = HybridRouter(
        settings,
        FakeClassifier(ClassifierOutput(intent="general_chat", tools=[], confidence=1.0)),
    )

    result = await router.route("hello")

    assert isinstance(result.source, RouteSource)
    assert result.source is RouteSource.CLASSIFIER
