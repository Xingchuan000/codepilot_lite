import pytest
from pydantic import ValidationError

from codepilot.policy import PolicyConfig, default_policy_config


def test_default_policy_config_contains_expected_rules() -> None:
    config = default_policy_config()

    assert config.tools.allow == ["list_files", "read_file", "search_code"]
    assert config.tools.ask == ["run_shell", "apply_patch", "replace_range"]
    assert ".env" in config.paths.deny
    assert "**/.env" in config.paths.deny
    assert "secrets" in config.paths.deny
    assert "secrets/**" in config.paths.deny
    assert ".ssh" in config.paths.deny
    assert ".ssh/**" in config.paths.deny
    assert "rm -rf" in config.commands.deny_substrings
    assert "git push" in config.commands.deny_substrings
    assert "npm publish" in config.commands.deny_substrings
    assert "curl " in config.commands.deny_substrings
    assert "wget " in config.commands.deny_substrings
    assert "ssh " in config.commands.deny_substrings
    assert "scp " in config.commands.deny_substrings
    assert "pytest" in config.commands.allow_prefixes
    assert "python -m pytest" in config.commands.allow_prefixes
    assert "ruff" in config.commands.allow_prefixes
    assert "mypy" in config.commands.allow_prefixes
    assert "npm test" in config.commands.allow_prefixes
    assert "npm run lint" in config.commands.allow_prefixes


def test_policy_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate({"unexpected": True})
