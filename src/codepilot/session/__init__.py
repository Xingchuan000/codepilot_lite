"""CodePilot Lite Session 的轻量入口。

这里只直接导出不会反向依赖 agent/router 的底层类型；更上层的 runtime、context、
permission、recovery 等模块改为按需加载，避免在导入 router 时形成循环导入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.session.artifacts import ArtifactStore, INLINE_CONTENT_MAX_CHARS, PersistedContent
from codepilot.session.database import SCHEMA_VERSION, SessionDatabase
from codepilot.session.ids import (
    make_artifact_id,
    make_attempt_id,
    make_event_id,
    make_message_id,
    make_part_id,
    make_project_id,
    make_session_id,
    make_tool_call_id,
    make_tool_result_id,
    make_turn_id,
    now_iso,
)
from codepilot.session.models import (
    ArtifactRecord,
    BranchCheckResult,
    BranchConfirmationRequired,
    ContextSummaryRecord,
    MessagePartRecord,
    MessageRecord,
    OpenedSession,
    PermissionGrantRecord,
    PermissionRequestRecord,
    PermissionResponseRecord,
    PendingTurnSubmission,
    ProjectRecord,
    RunAttemptRecord,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    SessionSummary,
    ToolCallRecord,
    ToolCallStatus,
    ToolResultRecord,
    ToolResultStatus,
    TurnRecord,
    TurnSubmission,
)
from codepilot.session.paths import SessionPaths, resolve_session_paths
from codepilot.session.store import SessionStore

if TYPE_CHECKING:
    from codepilot.session.compaction import CompactionService
    from codepilot.session.context import ContextAssembler
    from codepilot.session.exporter import SessionExporter
    from codepilot.session.permission import PermissionRequestContext, PermissionScope, PermissionScopeBuilder, SessionPermissionBroker
    from codepilot.session.recovery import RecoveryPlan, RecoveryService
    from codepilot.session.reconcilers import RecoveryDecision, ReconciliationResult
    from codepilot.session.runtime import SessionRuntime, TurnExecutionResult
    from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver
    from codepilot.session.model_capabilities import ModelContextProfile, resolve_model_context_profile
    from codepilot.session.service import CrossProviderSwitchNotSupported, SessionService

__all__ = [
    "ArtifactRecord",
    "ArtifactStore",
    "BranchCheckResult",
    "BranchConfirmationRequired",
    "CompactionService",
    "ContextAssembler",
    "ContextSummaryRecord",
    "CrossProviderSwitchNotSupported",
    "INLINE_CONTENT_MAX_CHARS",
    "MessagePartRecord",
    "MessageRecord",
    "ModelContextProfile",
    "OpenedSession",
    "PersistedContent",
    "PermissionGrantRecord",
    "PermissionRequestContext",
    "PermissionRequestRecord",
    "PermissionResponseRecord",
    "PermissionScope",
    "PermissionScopeBuilder",
    "PendingTurnSubmission",
    "ProjectRecord",
    "RecoveryDecision",
    "RecoveryPlan",
    "RecoveryService",
    "ReconciliationResult",
    "RunAttemptRecord",
    "SCHEMA_VERSION",
    "SessionDatabase",
    "SessionEventRecord",
    "SessionExporter",
    "SessionPaths",
    "SessionPermissionBroker",
    "SessionRecord",
    "SessionRuntime",
    "SessionService",
    "SessionStore",
    "SessionSummary",
    "SessionStatus",
    "SessionTraceRecorder",
    "SQLiteToolLifecycleObserver",
    "ToolCallRecord",
    "ToolCallStatus",
    "ToolResultRecord",
    "ToolResultStatus",
    "TurnRecord",
    "TurnSubmission",
    "TurnExecutionResult",
    "make_artifact_id",
    "make_attempt_id",
    "make_event_id",
    "make_message_id",
    "make_part_id",
    "make_project_id",
    "make_session_id",
    "make_tool_call_id",
    "make_tool_result_id",
    "make_turn_id",
    "now_iso",
    "resolve_model_context_profile",
    "resolve_session_paths",
]


def __getattr__(name: str):
    if name == "SessionRuntime":
        from codepilot.session.runtime import SessionRuntime

        return SessionRuntime
    if name == "TurnExecutionResult":
        from codepilot.session.runtime import TurnExecutionResult

        return TurnExecutionResult
    if name == "ContextAssembler":
        from codepilot.session.context import ContextAssembler

        return ContextAssembler
    if name == "SessionExporter":
        from codepilot.session.exporter import SessionExporter

        return SessionExporter
    if name == "SessionPermissionBroker":
        from codepilot.session.permission import SessionPermissionBroker

        return SessionPermissionBroker
    if name == "PermissionRequestContext":
        from codepilot.session.permission import PermissionRequestContext

        return PermissionRequestContext
    if name == "PermissionScope":
        from codepilot.session.permission import PermissionScope

        return PermissionScope
    if name == "PermissionScopeBuilder":
        from codepilot.session.permission import PermissionScopeBuilder

        return PermissionScopeBuilder
    if name == "RecoveryPlan":
        from codepilot.session.recovery import RecoveryPlan

        return RecoveryPlan
    if name == "RecoveryService":
        from codepilot.session.recovery import RecoveryService

        return RecoveryService
    if name == "CompactionService":
        from codepilot.session.compaction import CompactionService

        return CompactionService
    if name == "RecoveryDecision":
        from codepilot.session.reconcilers import RecoveryDecision

        return RecoveryDecision
    if name == "ReconciliationResult":
        from codepilot.session.reconcilers import ReconciliationResult

        return ReconciliationResult
    if name == "SQLiteToolLifecycleObserver":
        from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver

        return SQLiteToolLifecycleObserver
    if name == "SessionService":
        from codepilot.session.service import SessionService

        return SessionService
    if name == "CrossProviderSwitchNotSupported":
        from codepilot.session.service import CrossProviderSwitchNotSupported

        return CrossProviderSwitchNotSupported
    if name == "SessionTraceRecorder":
        from codepilot.session.trace_recorder import SessionTraceRecorder

        return SessionTraceRecorder
    if name == "ModelContextProfile":
        from codepilot.session.model_capabilities import ModelContextProfile

        return ModelContextProfile
    if name == "resolve_model_context_profile":
        from codepilot.session.model_capabilities import resolve_model_context_profile

        return resolve_model_context_profile
    raise AttributeError(name)
