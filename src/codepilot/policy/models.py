from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PolicyDecisionValue = Literal["allow", "ask", "deny"]
PolicyMode = Literal["read_only", "build", "danger"]


class PolicyDecision(BaseModel):
    """一次 ToolAction 的权限判断结果。"""

    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecisionValue
    reason: str
    tool_name: str | None = None
    matched_rule: str | None = None
    requires_approval: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """是否允许直接执行。"""

        return self.decision == "allow"

    @property
    def denied(self) -> bool:
        """是否明确拒绝。"""

        return self.decision == "deny"

    @property
    def asks(self) -> bool:
        """是否需要人工确认。"""

        return self.decision == "ask"


class PolicyContext(BaseModel):
    """PolicyChecker 判断 action 时需要的运行上下文。"""

    model_config = ConfigDict(extra="forbid")

    repo: str | Path | None = None
    mode: PolicyMode = "build"
    interactive: bool = False
    approved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
