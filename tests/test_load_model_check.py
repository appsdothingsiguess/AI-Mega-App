from types import SimpleNamespace

from scripts.load_model_check import select_alias


def model(name: str, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(name=name, enabled=enabled, class_="general", ctx=4096)


def test_select_alias_returns_numbered_enabled_model() -> None:
    models = [model("disabled", False), model("chat-default"), model("reasoner")]

    assert select_alias(models, input_fn=lambda _: "2") == "reasoner"


def test_select_alias_reprompts_invalid_input() -> None:
    answers = iter(["nope", "1"])

    assert select_alias([model("chat-default")], input_fn=lambda _: next(answers)) == "chat-default"


def test_select_alias_can_cancel() -> None:
    assert select_alias([model("chat-default")], input_fn=lambda _: "q") is None
