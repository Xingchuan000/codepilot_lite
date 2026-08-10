from __future__ import annotations

from dataclasses import dataclass

from codepilot.agent.boundary import AgentBoundary, AgentBoundaryResolver
from codepilot.multi_agent.models import AgentProfile
from codepilot.multi_agent.profiles import filter_builtin_specs, filter_scout_mcp_specs
from codepilot.router.router import ToolRouter
from codepilot.tools.base import ToolSideEffect


@dataclass(frozen=True)
class MultiAgentBoundaryResolver(AgentBoundaryResolver):
    profile: AgentProfile
    write_scope: tuple[str, ...] = ()

    def resolve(self, router: ToolRouter) -> AgentBoundary:
        visible = list(filter_builtin_specs(self.profile, router.list_visible_tool_specs()))
        if self.profile.allows_mcp and router.external_tool_registry is not None:
            mcp_specs = router.external_tool_registry.list_exposed_specs()
            if self.profile.name == "scout":
                mcp_specs = filter_scout_mcp_specs(mcp_specs)
            visible.extend(mcp_specs)
        if self.profile.name == "general" and not self.write_scope:
            visible = [spec for spec in visible if spec.side_effect != ToolSideEffect.LOCAL_WRITE]
        return AgentBoundary(
            instructions=self.profile.instructions,
            allowed_tool_names=frozenset(spec.name for spec in visible),
            write_scope=self.write_scope if self.profile.name == "general" else (),
        )
