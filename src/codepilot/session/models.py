from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal


SessionStatus = Literal["active", "archived"]
TurnStatus = Literal["queued", "running", "waiting_permission", "recovery_required", "completed", "failed", "cancelled", "interrupted"]
AttemptStatus = Literal["created", "running", "completed", "failed", "cancelled", "interrupted"]
MessageStatus = Literal["in_progress", "completed", "interrupted", "failed"]
MessageRole = Literal["system", "user", "assistant", "tool"]
MessagePartType = Literal["text", "reasoning", "tool_call", "tool_result", "approval", "summary", "system_event", "error"]
ToolCallStatus = Literal[
    "created",
    "approval_pending",
    "approved",
    "denied",
    "execution_started",
    "completed",
    "failed",
    "execution_uncertain",
    "recovery_required",
    "recovery_aborted",
]
ToolResultStatus = Literal["success", "failed", "denied", "interrupted", "execution_uncertain", "recovered_completed", "recovered_not_executed", "recovery_aborted"]


@dataclass(frozen=True)
class ProjectRecord:
    """仓库/项目的最小持久化记录。"""

    project_id: str
    path: Path
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SessionRecord:
    """Session 的主记录，SQLite 是唯一事实来源。"""

    session_id: str
    project_id: str
    title: str
    provider: str
    current_model: str
    permission_mode: str
    initial_branch: str | None
    current_branch: str | None
    status: SessionStatus
    parent_session_id: str | None
    forked_from_turn_id: str | None
    created_at: str
    updated_at: str
    last_activity_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionSummary:
    """用于列表页/选择器的轻量 Session 视图。"""

    session_id: str
    project_id: str
    title: str
    provider: str
    current_model: str
    permission_mode: str
    status: SessionStatus
    current_branch: str | None
    last_activity_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OpenedSession:
    """打开 Session 后给上层使用的路径和访问能力信息。"""

    session: SessionRecord
    project_path: Path
    project_exists: bool
    read_only: bool


@dataclass(frozen=True)
class BranchCheckResult:
    """创建 Turn 前对当前分支进行校验的结果。"""

    session_id: str
    expected_branch: str | None
    actual_branch: str | None
    changed: bool


@dataclass(frozen=True)
class BranchConfirmationRequired:
    """提示用户确认分支变化，不代表已经创建 Turn。"""

    session_id: str
    old_branch: str | None
    new_branch: str | None


@dataclass(frozen=True)
class PendingTurnSubmission:
    """等待用户确认分支变化的原始提交。

    文本保存在提交对象中，而不是从 TUI 已截断的 Transcript 反推，确保确认后提交的仍是
    用户最初输入的完整内容。该对象只表示待确认状态，本身不会写入 SQLite。
    """

    session_id: str
    text: str
    old_branch: str | None
    new_branch: str | None


@dataclass(frozen=True)
class TurnRecord:
    """一次用户提交对应的 Turn 主记录。"""

    turn_id: str
    session_id: str
    sequence: int
    title: str
    status: TurnStatus
    provider_snapshot: str
    model_snapshot: str
    permission_mode_snapshot: str
    branch_snapshot: str | None
    created_at: str
    updated_at: str
    last_activity_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunAttemptRecord:
    """一次 Turn 下的执行尝试。"""

    attempt_id: str
    turn_id: str
    attempt_number: int
    status: AttemptStatus
    created_at: str
    updated_at: str
    started_at: str | None
    ended_at: str | None
    interruption_reason: str | None = None
    worker_id: str | None = None
    lease_expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnSubmission:
    """一次已原子持久化的 Turn 及其首个执行 Attempt。"""

    turn: TurnRecord
    attempt: RunAttemptRecord


@dataclass(frozen=True)
class MessageRecord:
    """持久化消息主表记录。"""

    message_id: str
    session_id: str
    turn_id: str
    attempt_id: str | None
    role: MessageRole
    status: MessageStatus
    content: Any
    created_at: str
    updated_at: str
    interrupted_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MessagePartRecord:
    """消息的结构化分片，支持 text/reasoning/tool_call 等多种块。"""

    part_id: str
    message_id: str
    sequence: int
    type: MessagePartType
    content: Any
    provider_format: str | None
    replayable: bool
    created_at: str
    artifact_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallRecord:
    """工具调用主记录。"""

    tool_call_id: str
    turn_id: str
    attempt_id: str | None
    message_id: str | None
    status: ToolCallStatus
    tool_name: str
    arguments: dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    side_effect: str | None
    idempotency: str | None
    recovery_strategy: str | None
    recovery_token: dict[str, Any] | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultRecord:
    """工具结果主记录。"""

    tool_result_id: str
    tool_call_id: str
    status: ToolResultStatus
    content: Any
    created_at: str
    output_preview: str | None = None
    artifact_id: str | None = None
    error: str | None = None
    success: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionRequestRecord:
    """持久化的权限请求。"""

    request_id: str
    session_id: str | None
    turn_id: str | None
    attempt_id: str | None
    tool_call_id: str | None
    scope_key: str | None
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    status: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionResponseRecord:
    """持久化的权限响应。"""

    response_id: str
    request_id: str
    decision: str
    reason: str | None
    responded_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionGrantRecord:
    """Session 级别的长期授权记录。"""

    grant_id: str
    session_id: str
    scope_key: str
    tool_name: str | None = None
    scope_json: dict[str, Any] | None = None
    created_at: str
    revoked_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionEventRecord:
    """Session 级领域事件，用于回放和调试。"""

    event_id: str
    session_id: str
    sequence: int
    event_type: str
    created_at: str
    turn_id: str | None
    attempt_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextSummaryRecord:
    """压缩后的上下文摘要。"""

    summary_id: str
    session_id: str
    turn_id: str | None
    created_at: str
    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRecord:
    """Session 产物索引。"""

    artifact_id: str
    session_id: str
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_path: str
    created_at: str
    content: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def to_jsonable(value: Any) -> Any:
    """把 Path / dataclass 递归转换成可直接 JSON 编码的结构。"""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=repr)]
    return value
