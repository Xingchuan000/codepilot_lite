from __future__ import annotations

import sys

import pytest

import minisweagent.models
from codepilot.agent.runner import (
    ModelConfigurationRequired,
    resolve_codepilot_model_identity,
)


def test_identity_resolution_rejects_model_class_configuration() -> None:
    litellm_module_loaded = "minisweagent.models.litellm_model" in sys.modules
    with pytest.raises(ModelConfigurationRequired, match="model_class is no longer supported"):
        resolve_codepilot_model_identity(
            fake_responses=None,
            model="gpt-4o-mini",
            model_config=["model.model_name=anthropic/claude-sonnet", "model.model_class=deterministic"],
            environ={},
        )
    assert ("minisweagent.models.litellm_model" in sys.modules) == litellm_module_loaded


def test_identity_resolution_supports_fake_responses_without_model_configuration() -> None:
    assert (
        resolve_codepilot_model_identity(fake_responses="responses.jsonl", model=None, model_config=[], environ={}).provider
        == "fake"
    )


def test_identity_resolution_reports_missing_configuration() -> None:
    with pytest.raises(ModelConfigurationRequired, match="尚未配置模型"):
        resolve_codepilot_model_identity(fake_responses=None, model=None, model_config=[], environ={})


def test_identity_resolution_uses_minisweagent_default(monkeypatch) -> None:
    monkeypatch.delenv("MSWEA_MODEL_NAME", raising=False)
    monkeypatch.setattr(minisweagent.models, "get_model_name", lambda: "deepseek/deepseek-v4-flash")

    identity = resolve_codepilot_model_identity(fake_responses=None, model=None, model_config=[])

    assert identity.model == "deepseek/deepseek-v4-flash"
    assert identity.provider == "deepseek"
    assert identity.source == "minisweagent_default"
