from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.agent.actions import AgentActionParseError, AgentFinishAction, AgentToolCallAction, parse_agent_action
from codepilot.agent.state import AgentState

if TYPE_CHECKING:
    from codepilot.agent.loop import AgentRunResult as AgentRunResult
    from codepilot.agent.loop import MinimalAgentLoop as MinimalAgentLoop

__all__ = [
    "AgentActionParseError",
    "AgentFinishAction",
    "AgentRunResult",
    "AgentState",
    "AgentToolCallAction",
    "MinimalAgentLoop",
    "parse_agent_action",
]


def __getattr__(name: str):
    if name == "AgentRunResult":
        from codepilot.agent.loop import AgentRunResult

        return AgentRunResult
    if name == "MinimalAgentLoop":
        from codepilot.agent.loop import MinimalAgentLoop

        return MinimalAgentLoop
    raise AttributeError(name)
