from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.agent.actions import AgentFinishAction, AgentFinishArgs
from codepilot.agent.state import AgentState

if TYPE_CHECKING:
    from codepilot.agent.loop import AgentRunResult as AgentRunResult
    from codepilot.agent.loop import MinimalAgentLoop as MinimalAgentLoop
    from codepilot.agent.runner import run_agent_task as run_agent_task

__all__ = [
    "AgentFinishAction",
    "AgentFinishArgs",
    "AgentRunResult",
    "AgentState",
    "MinimalAgentLoop",
    "run_agent_task",
]


def __getattr__(name: str):
    if name == "AgentRunResult":
        from codepilot.agent.loop import AgentRunResult

        return AgentRunResult
    if name == "MinimalAgentLoop":
        from codepilot.agent.loop import MinimalAgentLoop

        return MinimalAgentLoop
    if name == "run_agent_task":
        from codepilot.agent.runner import run_agent_task

        return run_agent_task
    raise AttributeError(name)
