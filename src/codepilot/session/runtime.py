from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop, TurnExecutionContext
from codepilot.llm.types import CodePilotLLMClient
from codepilot.router import ToolRouter
from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.git_context import read_git_context
from codepilot.session.models import BranchConfirmationRequired, TurnRecord, TurnSubmission
from codepilot.session.permission import PermissionRequestContext
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore
from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver
from codepilot.session.trace_recorder import SessionTraceRecorder


_UNCONFIRMED_BRANCH = object()


@dataclass(frozen=True)
class TurnExecutionResult:
    """Runtime 对一次 Turn 的执行结果和 Agent 结果的薄封装。"""

    turn: TurnRecord
    result: AgentRunResult


class SessionRuntime:
    """把 Session 持久化生命周期接到现有单线程 AgentLoop。"""

    def __init__(self, database: SessionDatabase, llm: CodePilotLLMClient, router_factory: Callable[[Any], ToolRouter], max_steps: int = 12, trace_hook: Callable[[Any], None] | None = None) -> None:
        self.database = database
        self.store = SessionStore(database)
        self.service = SessionService(database)
        self.assembler = ContextAssembler(database, self.store)
        self.llm = llm
        self.router_factory = router_factory
        self.max_steps = max_steps
        self.trace_hook = trace_hook

    def submit_user_message(
        self,
        session_id: str,
        text: str,
        *,
        confirmed_branch: str | None | object = _UNCONFIRMED_BRANCH,
    ) -> TurnSubmission | BranchConfirmationRequired:
        """提交用户消息；分支变化确认前不写入任何 Turn 业务记录。

        用户确认后仍会重新读取 Git 的实际分支。只有确认值与本次读取值一致时，Store
        才会在单个事务中创建 Turn、User Message、Attempt 和对应事件。
        """

        opened = self.service.open_session(session_id)
        if opened.session.status != "active":
            raise ValueError("archived session is read-only")
        if not opened.project_exists:
            raise FileNotFoundError(opened.project_path)
        if any(turn.status in {"queued", "running", "waiting_permission"} for turn in self.store.list_turns(session_id)):
            raise RuntimeError("session already has a running turn")
        branch = self.service.validate_branch_before_turn(session_id)
        branch_confirmation_provided = confirmed_branch is not _UNCONFIRMED_BRANCH
        if branch.changed and not branch_confirmation_provided:
            return BranchConfirmationRequired(session_id, branch.expected_branch, branch.actual_branch)
        return self.store.create_turn_submission(
            session_id=session_id,
            text=text,
            actual_branch_reader=lambda: read_git_context(opened.project_path).branch,
            confirmed_branch=confirmed_branch if isinstance(confirmed_branch, str) or confirmed_branch is None else None,
            branch_confirmation_provided=branch_confirmation_provided,
        )

    def run_turn(self, turn_id: str, attempt_id: str, cancellation_token: Any | None = None) -> TurnExecutionResult:
        """从持久化 Turn 组装上下文并执行；终态始终写回 SQLite。"""

        turn = self.store.get_turn(turn_id)
        session = self.store.get_session(turn.session_id)
        opened = self.service.open_session(session.session_id)
        attempt = self.store.get_attempt(attempt_id)
        if attempt.turn_id != turn_id:
            raise ValueError("attempt does not belong to turn")
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
        self.store.start_turn_attempt(
            turn_id,
            attempt_id,
            worker_id=worker_id,
            lease_expires_at=_lease_expiry(),
        )
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=lambda: _renew_lease_until_stopped(self.store, attempt_id, worker_id, heartbeat_stop, lease_lost),
            daemon=True,
        )
        heartbeat.start()
        try:
            trace = SessionTraceRecorder(self.database, session.session_id, turn_id, attempt.attempt_id, record_hook=self.trace_hook)
            router = self.router_factory(trace)
            if hasattr(router, "permission_request_context"):
                router.permission_request_context = PermissionRequestContext(session.session_id, turn_id, attempt.attempt_id, None)
            # Trace 只记录事件；业务表由稳定 ID 的 Lifecycle Observer 单独维护。
            router.lifecycle_observer = SQLiteToolLifecycleObserver(self.database, session.session_id, turn_id, attempt_id)
            loop = MinimalAgentLoop(
                llm=self.llm,
                router=router,
                trace_logger=trace,
                max_steps=self.max_steps,
                cancellation_token=_LeaseAwareCancellationToken(cancellation_token, lease_lost),
                event_sink=trace,
            )
            task = next(message.content for message, _ in self.store.list_messages_with_parts(session.session_id, turn_id) if message.role == "user")
            context = ContextAssembler(self.database, self.store).build(session.session_id, turn_id, turn.provider_snapshot, turn.model_snapshot)
            result = loop.run_turn(TurnExecutionContext(session.session_id, turn_id, attempt.attempt_id, str(task), opened.project_path, context))
        except Exception as exc:
            heartbeat_stop.set()
            heartbeat.join()
            self.store.interrupt_turn_attempt(turn_id, attempt_id, str(exc), worker_id=worker_id)
            raise
        heartbeat_stop.set()
        heartbeat.join()
        if result.status in {"success", "message_complete"}:
            self.store.finish_turn_attempt(turn_id, attempt_id, attempt_status="completed", turn_status="completed", worker_id=worker_id)
        elif result.status == "cancelled":
            self.store.finish_turn_attempt(turn_id, attempt_id, attempt_status="cancelled", turn_status="cancelled", worker_id=worker_id)
        else:
            self.store.finish_turn_attempt(turn_id, attempt_id, attempt_status="failed", turn_status="failed", worker_id=worker_id)
        return TurnExecutionResult(self.store.get_turn(turn_id), result)


def _lease_expiry() -> str:
    return (datetime.now(UTC) + timedelta(minutes=2)).isoformat()


class _LeaseAwareCancellationToken:
    def __init__(self, external: Any | None, lease_lost: threading.Event) -> None:
        self.external = external
        self.lease_lost = lease_lost

    def is_cancelled(self) -> bool:
        return self.lease_lost.is_set() or bool(self.external and self.external.is_cancelled())


def _renew_lease_until_stopped(
    store: SessionStore,
    attempt_id: str,
    worker_id: str,
    stop: threading.Event,
    lease_lost: threading.Event,
) -> None:
    while not stop.wait(30):
        while not stop.is_set():
            try:
                store.renew_attempt_lease(attempt_id, worker_id, _lease_expiry())
                break
            except RuntimeError:
                lease_lost.set()
                return
            except Exception:
                # SQLite 短暂 busy/IO 错误每秒重试，不能让 heartbeat 静默消失。
                if stop.wait(1):
                    return
