from __future__ import annotations

import pytest

import codepilot.llm.model_capabilities as model_capabilities


def test_litellm_capability_lookup_returns_typed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_capabilities.litellm,
        "get_model_info",
        lambda *, model: {"max_input_tokens": 32_000, "max_output_tokens": 2_000},
    )

    result = model_capabilities.resolve_litellm_model_capabilities(provider="openai", model="test-model")

    assert result is not None
    assert result.max_context_tokens == 32_000
    assert result.max_output_tokens == 2_000
    assert result.source == "litellm_model_info"


def test_litellm_unknown_model_uses_conservative_fallback_but_programmer_error_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_capabilities.litellm,
        "get_model_info",
        lambda *, model: (_ for _ in ()).throw(Exception("This model isn't mapped yet.")),
    )
    assert model_capabilities.resolve_litellm_model_capabilities(provider="openai", model="unknown") is None

    monkeypatch.setattr(
        model_capabilities.litellm,
        "get_model_info",
        lambda *, model: (_ for _ in ()).throw(RuntimeError("metadata adapter bug")),
    )
    with pytest.raises(RuntimeError, match="metadata adapter bug"):
        model_capabilities.resolve_litellm_model_capabilities(provider="openai", model="broken")
