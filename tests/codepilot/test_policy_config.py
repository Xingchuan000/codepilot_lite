import pytest
from pydantic import ValidationError

from codepilot.policy import PolicyConfig, default_policy_config


def test_default_policy_config_contains_expected_rules() -> None:
    config = default_policy_config()

    assert ".env" in config.paths.deny
    assert "**/.env" in config.paths.deny
    assert "secrets" in config.paths.deny
    assert "secrets/**" in config.paths.deny
    assert ".ssh" in config.paths.deny
    assert ".ssh/**" in config.paths.deny
    assert config.commands.hard_deny_patterns == ["rm -rf /", "rm -rf ~"]


def test_policy_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate({"unexpected": True})
