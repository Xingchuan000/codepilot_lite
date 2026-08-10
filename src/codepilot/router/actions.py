from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from codepilot.tools.base import ToolResult


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
