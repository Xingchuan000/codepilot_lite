from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codepilot.tools.base import ToolResult


class ToolAction(BaseModel):
    """一次准备交给 ToolRouter 执行的结构化工具动作。"""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=lambda: f"act-{uuid4().hex[:12]}")
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool_name must not be empty")
        return value


class ToolRouteResult(BaseModel):
    """ToolRouter 执行一次 action 后的统一返回结果。"""

    model_config = ConfigDict(extra="forbid")

    action_id: str
    tool_name: str
    success: bool
    result: ToolResult
    trace_path: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
