from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from codepilot.agent.runner import build_codepilot_llm
from codepilot.permissions import PermissionResponse
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.session.models import BranchConfirmationRequired, PendingTurnSubmission, SessionRecord
from codepilot.session.permission import SessionPermissionBroker
from codepilot.session.runtime import SessionRuntime
from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.event_stream import MemoryEventStream, trace_event_to_tui_event
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUIEvent
from codepilot.tui_agent.permission_broker import AutoApproveLocalWriteBroker, PermissionBroker, NonInteractiveBroker
from codepilot.tui_agent.session_store import SessionStore, now_iso


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


@dataclass(frozen=True)
class TUIRunnerConfig:
    model: str | None
    model_config: tuple[str, ...]
    permission_mode: PermissionMode
    fake_actions: str | Path | None
    mcp_config: str | Path | None
    max_steps: int
    auto_report: bool = False


RunnerFailureSource = Literal["runner_setup", "agent_runtime"]
_UNCONFIRMED_BRANCH = object()


def _policy_context_for_mode(mode: PermissionMode, repo: Path) -> PolicyContext:
    if mode == "read_only":
        return PolicyContext(repo=repo, mode="read_only", approved=False, interactive=True)
    if mode == "unsafe_auto":
        return PolicyContext(repo=repo, mode="danger", approved=True, interactive=True)
    return PolicyContext(repo=repo, mode="build", approved=False, interactive=True)


class TUIAgentRunner:
    """TUI 到 SessionRuntime 的单线程适配器，不拥有第二份 Session 状态。"""

    def __init__(self, *, project: ProjectContext, session: SessionRecord | None, session_store: SessionStore, event_stream: MemoryEventStream, permission_broker: PermissionBroker | None, config: TUIRunnerConfig) -> None:
        self.project = project
        self.session = session
        self.session_store = session_store
        self.event_stream = event_stream
        self.base_permission_broker = permission_broker or NonInteractiveBroker()
        self.mode_permission_broker = self.base_permission_broker
        self.permission_broker = self.base_permission_broker
        self.session_permission_broker: SessionPermissionBroker | None = None
        self.config = config
        self.active_session_id = session.session_id if session is not None else None
        self.active_turn_id: str | None = None
        self.cancellation_token = CancellationToken()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.set_permission_mode(config.permission_mode)

    def _publish_trace_event(self, event: TraceEvent) -> None:
        tui_event = trace_event_to_tui_event(event)
        if tui_event is not None:
            self.event_stream.publish(tui_event)

    def _runtime(self) -> SessionRuntime:
        mcp_registry = MCPToolRegistry.from_config(self.config.mcp_config) if self.config.mcp_config else None
        policy_checker = PolicyChecker.default(extra_tool_specs={item.name: item for item in mcp_registry.list_specs()}) if mcp_registry else PolicyChecker.default()
        llm = build_codepilot_llm(fake_actions=self.config.fake_actions, model=self.config.model, model_config=list(self.config.model_config))
        self.mode_permission_broker = AutoApproveLocalWriteBroker(self.base_permission_broker) if self.config.permission_mode == "accept_edits" else self.base_permission_broker
        if self.active_session_id is not None:
            self.session_permission_broker = SessionPermissionBroker(self.session_store.database, self.active_session_id, self.mode_permission_broker)
            self.permission_broker = self.session_permission_broker
        else:
            self.session_permission_broker = None
            self.permission_broker = self.mode_permission_broker

        def router_factory(trace):
            return ToolRouter(
                trace_logger=trace,
                policy_checker=policy_checker,
                policy_context=_policy_context_for_mode(self.config.permission_mode, self.project.effective_repo_path),
                external_tool_registry=mcp_registry,
                permission_broker=self.permission_broker,
            )

        return SessionRuntime(self.session_store.database, llm, router_factory, max_steps=self.config.max_steps, trace_hook=self._publish_trace_event)

    def _session_broker(self) -> SessionPermissionBroker:
        if self.active_session_id is None:
            raise RuntimeError("select or create a session before accessing permission state")
        if self.session_permission_broker is None or self.session_permission_broker.session_id != self.active_session_id:
            self.session_permission_broker = SessionPermissionBroker(self.session_store.database, self.active_session_id, self.mode_permission_broker)
        self.permission_broker = self.session_permission_broker
        return self.session_permission_broker

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.config = replace(self.config, permission_mode=mode)
        self.mode_permission_broker = AutoApproveLocalWriteBroker(self.base_permission_broker) if mode == "accept_edits" else self.base_permission_broker
        if self.active_session_id is not None:
            self.session_permission_broker = SessionPermissionBroker(self.session_store.database, self.active_session_id, self.mode_permission_broker)
            self.permission_broker = self.session_permission_broker
            return
        self.permission_broker = self.mode_permission_broker

    def _run_task_worker(self, task: str, confirmed_branch: str | None | object = _UNCONFIRMED_BRANCH) -> None:
        turn_id: str | None = None
        attempt_id: str | None = None
        try:
            if self.active_session_id is None:
                raise RuntimeError("select or create a session before submitting a task")
            runtime = self._runtime()
            submission = (
                runtime.submit_user_message(self.active_session_id, task)
                if confirmed_branch is _UNCONFIRMED_BRANCH
                else runtime.submit_user_message(self.active_session_id, task, confirmed_branch=confirmed_branch)
            )
            if isinstance(submission, BranchConfirmationRequired):
                pending = PendingTurnSubmission(
                    session_id=submission.session_id,
                    text=task,
                    old_branch=submission.old_branch,
                    new_branch=submission.new_branch,
                )
                # 先释放旧工作线程，再通知 UI；用户快速确认时才能立即启动恢复提交线程。
                self._clear_current_worker()
                self.event_stream.publish(
                    TUIEvent(
                        type="branch_confirmation_required",
                        timestamp=now_iso(),
                        session_id=pending.session_id,
                        payload={
                            "text": pending.text,
                            "old_branch": pending.old_branch,
                            "new_branch": pending.new_branch,
                        },
                    )
                )
                return
            # User Message 已经和 Turn 一起提交后才发布，取消分支确认时不会留下内存幽灵消息。
            self.event_stream.publish(TUIEvent(type="user_message", timestamp=now_iso(), session_id=self.active_session_id, payload={"text": task}))
            turn_id = submission.turn.turn_id
            attempt_id = submission.attempt.attempt_id
            self.active_turn_id = turn_id
            execution = runtime.run_turn(turn_id, attempt_id, self.cancellation_token)
            self.event_stream.publish(TUIEvent(type="run_finished", timestamp=now_iso(), session_id=self.active_session_id, payload={"status": execution.result.status, "success": execution.result.success, "turn_id": turn_id, "attempt_id": attempt_id}))
        except Exception as exc:
            payload = {"error": str(exc), "source": "agent_runtime"}
            if turn_id is not None:
                payload["turn_id"] = turn_id
            if attempt_id is not None:
                payload["attempt_id"] = attempt_id
            self.event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), session_id=self.active_session_id, payload=payload))
            self.event_stream.publish(TUIEvent(type="run_finished", timestamp=now_iso(), session_id=self.active_session_id, payload={"status": "interrupted", "success": False, "turn_id": turn_id, "attempt_id": attempt_id}))
        finally:
            self.active_turn_id = None
            self._clear_current_worker()

    def _clear_current_worker(self) -> None:
        """只清理当前线程自己的槽位，禁止旧线程覆盖刚启动的新线程。"""

        with self._lock:
            if self._thread is threading.current_thread():
                self._thread = None

    def start_task(self, task: str, *, confirmed_branch: str | None | object = _UNCONFIRMED_BRANCH) -> str:
        with self._lock:
            if self.active_session_id is None:
                raise RuntimeError("select or create a session before submitting a task")
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("a task is already running")
            self.cancellation_token = CancellationToken()
            self._thread = threading.Thread(target=self._run_task_worker, args=(task, confirmed_branch), daemon=True)
            self._thread.start()
            return f"turn-pending-{self.active_session_id[:12]}"

    def resume_after_branch_confirmation(self, pending: PendingTurnSubmission) -> str:
        """使用用户确认的分支恢复原始提交，不绕过 Runtime 的再次分支校验。"""

        if pending.session_id != self.active_session_id:
            raise RuntimeError("branch confirmation does not belong to the active session")
        return self.start_task(pending.text, confirmed_branch=pending.new_branch)

    def _run_recovery_worker(self, turn_id: str, attempt_id: str) -> None:
        try:
            if self.active_session_id is None:
                raise RuntimeError("select a session before recovery")
            execution = self._runtime().run_turn(turn_id, attempt_id, self.cancellation_token)
            self.event_stream.publish(
                TUIEvent(
                    type="run_finished",
                    timestamp=now_iso(),
                    session_id=self.active_session_id,
                    payload={
                        "status": execution.result.status,
                        "success": execution.result.success,
                        "turn_id": turn_id,
                        "attempt_id": attempt_id,
                    },
                )
            )
        except Exception as exc:
            self.event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), session_id=self.active_session_id, payload={"error": str(exc), "source": "agent_runtime"}))
        finally:
            self.active_turn_id = None
            self._clear_current_worker()

    def resume_turn(self, turn_id: str, attempt_id: str) -> None:
        """执行 RecoveryService 已原子创建的 Attempt，不创建新的用户 Turn。"""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("a task is already running")
            self.cancellation_token = CancellationToken()
            self.active_turn_id = turn_id
            self._thread = threading.Thread(target=self._run_recovery_worker, args=(turn_id, attempt_id), daemon=True)
            self._thread.start()

    def cancel_current(self) -> None:
        self.cancellation_token.cancel()
        self.permission_broker.cancel_all("cancelled from TUI")

    def restore_pending_permission(self, request_id: str):
        return self._session_broker().restore_pending_request(request_id)

    def resolve_permission(self, response: PermissionResponse) -> None:
        self._session_broker().resolve(response)

    def abort_pending_permission(self, request_id: str) -> None:
        self._session_broker().resolve(PermissionResponse(request_id=request_id, decision="deny", reason="aborted from TUI", responded_at=now_iso()))

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())
