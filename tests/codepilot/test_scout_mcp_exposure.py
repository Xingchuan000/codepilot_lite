from __future__ import annotations

from codepilot.multi_agent.profiles import filter_scout_mcp_specs
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.tools.actions import ToolAction
from codepilot.tools.base import ExternalImpact, Reversibility, ToolRisk, ToolSideEffect, ToolSpec


def _mcp_spec(name: str, side_effect: ToolSideEffect, impact: ExternalImpact, reversibility: Reversibility) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        risk=ToolRisk.NETWORK if side_effect == ToolSideEffect.NETWORK else ToolRisk.READ_ONLY,
        side_effect=side_effect,
        external_impact=impact,
        reversibility=reversibility,
    )


def test_scout_exposes_non_external_mcp_tools() -> None:
    specs = filter_scout_mcp_specs(
        [
            _mcp_spec("mcp.docs.read", ToolSideEffect.NONE, ExternalImpact.NONE, Reversibility.NOT_APPLICABLE),
            _mcp_spec("mcp.web.query", ToolSideEffect.NETWORK, ExternalImpact.READ, Reversibility.NOT_APPLICABLE),
            _mcp_spec("mcp.fs.write", ToolSideEffect.LOCAL_WRITE, ExternalImpact.NONE, Reversibility.UNKNOWN),
            _mcp_spec("mcp.exec.run", ToolSideEffect.LOCAL_EXEC, ExternalImpact.NONE, Reversibility.UNKNOWN),
            _mcp_spec("mcp.publish", ToolSideEffect.EXTERNAL, ExternalImpact.WRITE, Reversibility.UNKNOWN),
        ]
    )

    assert [spec.name for spec in specs] == ["mcp.docs.read", "mcp.web.query"]


def test_scout_policy_metadata_denies_hidden_write_tool() -> None:
    checker = PolicyChecker.default()
    context = PolicyContext(
        mode="build",
        metadata={"allowed_tools": ["mcp.docs.read", "mcp.web.query"]},
    )

    decision = checker.check(
        ToolAction(tool_name="mcp.fs.write", arguments={}),
        context=context,
        spec=_mcp_spec("mcp.fs.write", ToolSideEffect.LOCAL_WRITE, ExternalImpact.NONE, Reversibility.UNKNOWN),
    )

    assert decision.denied
    assert decision.matched_rule == "agent.profile.tool.deny"
