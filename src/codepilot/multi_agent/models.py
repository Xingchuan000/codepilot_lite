from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AgentType = Literal["general", "explore", "scout"]
AgentStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "recovery_required",
]


@dataclass(frozen=True)
class AgentProfile:
    name: AgentType
    instructions: str
    allowed_builtin_tools: frozenset[str]
    allows_mcp: bool
    supports_write: bool
    memory_read: bool = True
    memory_write: bool = False
    can_spawn: bool = False


@dataclass(frozen=True)
class SpawnContract:
    agent_type: AgentType
    task: str
    write_scope: tuple[str, ...] = ()
    context_mode: str = "summary_recent"
    recent_turns: int = 3


@dataclass
class AgentHandle:
    child_session_id: str
    parent_session_id: str
    parent_turn_id: str
    agent_type: AgentType
    write_scope: tuple[str, ...] = ()
    workspace_path: Path | None = None
    patch_artifact_id: str | None = None
    status: AgentStatus = "queued"
    error: str | None = None
