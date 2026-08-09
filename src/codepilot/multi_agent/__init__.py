"""Primary/Child agent control-plane primitives."""

from codepilot.multi_agent.models import AgentHandle, AgentProfile, AgentStatus, AgentType, SpawnContract
from codepilot.multi_agent.profiles import (
    EXPLORE_PROFILE,
    GENERAL_PROFILE,
    SCOUT_PROFILE,
    filter_builtin_specs,
    filter_scout_mcp_specs,
    get_agent_profile,
)
from codepilot.multi_agent.supervisor import AgentSupervisor, AgentSupervisorConfig, path_allowed, scopes_may_overlap

__all__ = [
    "AgentHandle",
    "AgentProfile",
    "AgentStatus",
    "AgentType",
    "SpawnContract",
    "GENERAL_PROFILE",
    "EXPLORE_PROFILE",
    "SCOUT_PROFILE",
    "filter_builtin_specs",
    "filter_scout_mcp_specs",
    "get_agent_profile",
    "AgentSupervisor",
    "AgentSupervisorConfig",
    "path_allowed",
    "scopes_may_overlap",
]
