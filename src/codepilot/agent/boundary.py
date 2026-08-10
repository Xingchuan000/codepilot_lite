from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codepilot.router.runtime_tools import RuntimeToolRegistry
from codepilot.router.router import ToolRouter


@dataclass(frozen=True)
class AgentBoundary:
    instructions: str | None
    allowed_tool_names: frozenset[str]
    write_scope: tuple[str, ...] = ()


class AgentBoundaryResolver(Protocol):
    def resolve(self, router: ToolRouter) -> AgentBoundary: ...


@dataclass(frozen=True)
class RuntimeToolContext:
    parent_session_id: str
    parent_turn_id: str
    parent_attempt_id: str
    parent_repo: Path


RuntimeToolRegistryFactory = Callable[[RuntimeToolContext], RuntimeToolRegistry]
