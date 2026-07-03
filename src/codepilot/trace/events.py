from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


TraceEventType = Literal[
    "run_start",
    "llm_call",
    "agent_action",
    "policy_decision",
    "tool_call",
    "tool_result",
    "agent_observation",
    "agent_finish",
    "run_end",
]


class TraceEvent(BaseModel):
    """CodePilot Lite 的结构化 trace 事件。"""

    schema_version: str = "trace.v1"
    run_id: str
    step: int
    event_type: TraceEventType
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # 工具相关字段在非工具事件里可以为空。
    tool_name: str | None = None
    risk: str | None = None
    side_effect: str | None = None
    default_permission: str | None = None
    policy_decision: str | None = None
    policy_reason: str | None = None
    policy_rule: str | None = None
    policy_mode: str | None = None

    # 输入输出保持机器可读，方便后续审计和检索。
    input: dict[str, Any] = Field(default_factory=dict)
    success: bool | None = None
    output_summary: str | None = None
    output_preview: str | None = None
    error: str | None = None

    # 额外信息统一放在 metadata 中，避免核心字段膨胀。
    metadata: dict[str, Any] = Field(default_factory=dict)
