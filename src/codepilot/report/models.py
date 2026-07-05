from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolStepReport(BaseModel):
    """报告里的一条工具或权限时间线。

    这里不是原始 trace event 的镜像，而是面向人工审查的最小摘要。
    """

    step: int | None = None
    tool_name: str
    success: bool | None = None
    policy_decision: str | None = None
    approved: bool | None = None
    executed: bool | None = None
    summary: str | None = None
    error: str | None = None
    risk_level: str | None = None
    side_effect: str | None = None
    arguments_preview: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TestReport(BaseModel):
    status: str | None = None
    command: str | None = None
    original_command: str | None = None
    executed_command: str | None = None
    failed_tests: list[str] = Field(default_factory=list)
    summary: str | None = None
    returncode: int | None = None
    timed_out: bool | None = None


class PolicyViolationReport(BaseModel):
    step: int | None = None
    tool_name: str | None = None
    decision: str | None = None
    reason: str | None = None
    rule: str | None = None
    approved: bool | None = None
    executed: bool | None = None


class PolicyReport(BaseModel):
    total: int = 0
    allowed: int = 0
    asked: int = 0
    denied: int = 0
    approved: int = 0
    violations: list[PolicyViolationReport] = Field(default_factory=list)


class DiffReport(BaseModel):
    checked: bool = False
    paths: list[str] = Field(default_factory=list)
    summary: str | None = None
    preview: str | None = None
    truncated: bool = False


class RunReport(BaseModel):
    run_id: str
    task: str | None = None
    repo: str | None = None
    model: str | None = None
    policy_mode: str | None = None
    max_steps: int | None = None
    status: str | None = None
    success: bool | None = None
    final_summary: str | None = None
    steps: int = 0
    changed_files: list[str] = Field(default_factory=list)
    tool_steps: list[ToolStepReport] = Field(default_factory=list)
    tests: TestReport = Field(default_factory=TestReport)
    policy: PolicyReport = Field(default_factory=PolicyReport)
    diff: DiffReport = Field(default_factory=DiffReport)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace_path: str | None = None
