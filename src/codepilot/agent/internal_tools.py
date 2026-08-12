from __future__ import annotations

from codepilot.agent.actions import AgentFinishArgs
from codepilot.tools.base import ExternalImpact, Reversibility, ToolIdempotency, ToolRecoveryStrategy, ToolRisk, ToolSideEffect, ToolSpec

CODEPILOT_FINISH_TOOL_NAME = "codepilot_finish"

CODEPILOT_FINISH_TOOL_SPEC = ToolSpec(
    name=CODEPILOT_FINISH_TOOL_NAME,
    description="Finish a repository task after the required work and validation are complete.",
    risk=ToolRisk.READ_ONLY,
    side_effect=ToolSideEffect.NONE,
    external_impact=ExternalImpact.NONE,
    reversibility=Reversibility.NOT_APPLICABLE,
    idempotency=ToolIdempotency.SAFE,
    recovery_strategy=ToolRecoveryStrategy.AUTO_RETRY,
    input_schema=AgentFinishArgs.model_json_schema(),
    metadata={"internal": True},
)
