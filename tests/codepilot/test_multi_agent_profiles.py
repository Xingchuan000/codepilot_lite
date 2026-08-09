from __future__ import annotations

import pytest

from codepilot.multi_agent.profiles import (
    EXPLORE_PROFILE,
    GENERAL_PROFILE,
    SCOUT_PROFILE,
    filter_builtin_specs,
    get_agent_profile,
)
from codepilot.tools.registry import list_tool_specs


def test_profiles_have_distinct_code_level_tool_views() -> None:
    assert "run_shell" not in GENERAL_PROFILE.allowed_builtin_tools
    assert "replace_range" in GENERAL_PROFILE.allowed_builtin_tools
    assert "replace_range" not in EXPLORE_PROFILE.allowed_builtin_tools
    assert "run_tests" not in EXPLORE_PROFILE.allowed_builtin_tools
    assert SCOUT_PROFILE.allows_mcp is True
    assert SCOUT_PROFILE.supports_write is False
    assert GENERAL_PROFILE.memory_write is False

    explore_names = {spec.name for spec in filter_builtin_specs(EXPLORE_PROFILE, list_tool_specs())}
    assert explore_names == EXPLORE_PROFILE.allowed_builtin_tools


def test_profile_resolver_rejects_unknown_role() -> None:
    assert get_agent_profile("general") is GENERAL_PROFILE

    with pytest.raises(ValueError, match="unsupported agent type"):
        get_agent_profile("unknown")
