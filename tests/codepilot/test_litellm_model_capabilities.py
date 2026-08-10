from __future__ import annotations

from codepilot.agent.runner import build_codepilot_llm
from codepilot.llm.model_capabilities import resolve_litellm_model_capabilities


def test_resolver_uses_litellm_model_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "codepilot.llm.model_capabilities.litellm.get_model_info",
        lambda model: {"max_input_tokens": 128_000, "max_output_tokens": 8_192},
    )

    capabilities = resolve_litellm_model_capabilities(provider="deepseek", model="deepseek-chat")

    assert capabilities is not None
    assert capabilities.max_context_tokens == 128_000
    assert capabilities.max_output_tokens == 8_192
    assert capabilities.source == "litellm_model_info"


def test_missing_litellm_metadata_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(
        "codepilot.llm.model_capabilities.litellm.get_model_info",
        lambda model: {"max_input_tokens": None},
    )

    assert resolve_litellm_model_capabilities(provider="custom", model="custom-model") is None


def test_explicit_model_capability_config_overrides_litellm_metadata(monkeypatch) -> None:
    monkeypatch.setattr("codepilot.agent.runner.litellm.supports_function_calling", lambda model: True)
    monkeypatch.setattr(
        "codepilot.llm.model_capabilities.litellm.get_model_info",
        lambda model: {"max_input_tokens": 16_384, "max_output_tokens": 4_096},
    )

    built = build_codepilot_llm(
        model="custom/model",
        model_config=[
            "model_capabilities.max_input_tokens=131072",
            "model_capabilities.max_output_tokens=8192",
        ],
    )

    assert built.capabilities is not None
    assert built.capabilities.max_context_tokens == 131_072
    assert built.capabilities.max_output_tokens == 8_192
    assert built.capabilities.source == "config"
