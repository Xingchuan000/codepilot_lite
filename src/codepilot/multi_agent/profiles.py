from __future__ import annotations

from collections.abc import Sequence

from codepilot.multi_agent.models import AgentProfile, AgentType
from codepilot.tools.base import DefaultPermission, ToolSideEffect, ToolSpec

GENERAL_PROFILE = AgentProfile(
    name="general",
    instructions=(
        "You are a delegated general coding worker. Complete only the assigned task. "
        "Do not expand scope. If write_scope is provided, modify only those paths. "
        "Return a concise result with changed files, tests run, unresolved risks, and evidence."
    ),
    allowed_builtin_tools=frozenset(
        {
            "list_files",
            "read_file",
            "search_code",
            "git_status",
            "git_diff",
            "replace_range",
            "apply_patch",
            "run_tests",
        }
    ),
    allows_mcp=False,
    supports_write=True,
)

EXPLORE_PROFILE = AgentProfile(
    name="explore",
    instructions=(
        "You are a read-only repository explorer. Locate implementations, call paths, tests, "
        "dependencies, and likely change points. Never modify files or execute write-capable tools."
    ),
    allowed_builtin_tools=frozenset(
        {"list_files", "read_file", "search_code", "git_status", "git_diff"}
    ),
    allows_mcp=False,
    supports_write=False,
)

SCOUT_PROFILE = AgentProfile(
    name="scout",
    instructions=(
        "You are a research scout. Focus on external documentation, upstream APIs, dependency behavior, "
        "and read-only local context needed to ground the research. Do not change local or external state."
    ),
    allowed_builtin_tools=frozenset({"list_files", "read_file", "search_code"}),
    allows_mcp=True,
    supports_write=False,
)

_PROFILES: dict[AgentType, AgentProfile] = {
    "general": GENERAL_PROFILE,
    "explore": EXPLORE_PROFILE,
    "scout": SCOUT_PROFILE,
}


def get_agent_profile(name: str) -> AgentProfile:
    try:
        return _PROFILES[name]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"unsupported agent type: {name}") from exc


def filter_builtin_specs(profile: AgentProfile, specs: Sequence[ToolSpec]) -> tuple[ToolSpec, ...]:
    return tuple(spec for spec in specs if spec.name in profile.allowed_builtin_tools)


def filter_scout_mcp_specs(specs: Sequence[ToolSpec]) -> tuple[ToolSpec, ...]:
    return tuple(
        spec
        for spec in specs
        if spec.side_effect in {ToolSideEffect.NONE, ToolSideEffect.NETWORK}
        and spec.default_permission != DefaultPermission.DENY
    )
