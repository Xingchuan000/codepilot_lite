from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from codepilot.agent.runner import build_codepilot_llm, resolve_codepilot_model_identity
from codepilot.agent.boundary import RuntimeToolContext
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.multi_agent.profiles import get_agent_profile
from codepilot.multi_agent.boundary import MultiAgentBoundaryResolver
from codepilot.multi_agent.runtime_tools import build_agent_control_registry
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.permissions import PermissionResponse
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.router.errors import ToolExecutionUncertainError
from codepilot.session.models import BranchConfirmationRequired, PendingTurnSubmission, SessionRecord
from codepilot.session.permission import SessionPermissionBroker
from codepilot.session.runtime import SessionRuntime
from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.event_stream import MemoryEventStream, trace_event_to_tui_event
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUIEvent
from codepilot.tui_agent.permission_broker import AutoApproveLocalWriteBroker, NonInteractiveBroker, PermissionBroker
from codepilot.tui_agent.session_controller import SessionController, now_iso


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
    fake_responses: str | Path | None
    mcp_config: str | Path | None
    max_steps: int


RunnerFailureSource = Literal["runner_setup", "agent_runtime"]
_UNCONFIRMED_BRANCH = object()


def _policy_context_for_mode(mode: PermissionMode, repo: Path) -> PolicyContext:
    if mode == "read_only":
        return PolicyContext(repo=repo, mode="read_only", approved=False, interactive=True)
    if mode == "unsafe_auto":
        return PolicyContext(repo=repo, mode="danger", approved=True, interactive=True)
    return PolicyContext(repo=repo, mode="build", approved=False, interactive=True)


def _policy_context_for_agent(mode: PermissionMode, repo: Path, profile) -> PolicyContext:
    """Derive the execution mode without weakening the profile tool allow-list.

    Explore should stay read-only even when the Primary is in a build mode. Scout
    must be able to use read-only NETWORK tools, so a read_only Primary cannot be
    mapped to PolicyChecker.read_only because that mode denies every NETWORK side
    effect before the Scout allow-list is considered. General keeps the parent
    ceiling unchanged.
    """

    if profile is not None and profile.name == "explore":
        return PolicyContext(repo=repo, mode="read_only", approved=False, interactive=True)
    if profile is not None and profile.name == "scout":
        if mode == "unsafe_auto":
            return PolicyContext(repo=repo, mode="danger", approved=True, interactive=True)
        return PolicyContext(repo=repo, mode="build", approved=False, interactive=True)
    return _policy_context_for_mode(mode, repo)


class TUIAgentRunner:
    """TUI 到 SessionRuntime 的单线程适配器，不拥有第二份 Session 状态。"""

    def __init__(self, *, project: ProjectContext, session: SessionRecord | None, session_controller: SessionController, event_stream: MemoryEventStream, permission_broker: PermissionBroker | None, config: TUIRunnerConfig) -> None:
        self.project = project
        self.session = session
        self.session_controller = session_controller
        self.event_stream = event_stream
        self.base_permission_broker = permission_broker or NonInteractiveBroker()
        self.mode_permission_broker = self.base_permission_broker
        self.permission_broker = self.base_permission_broker
        self.session_permission_broker: SessionPermissionBroker | None = None
        self._session_permission_brokers: dict[str, SessionPermissionBroker] = {}
        self.config = config
        self.active_session_id = session.session_id if session is not None else None
        self.active_turn_id: str | None = None
        self.cancellation_token = CancellationToken()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.agent_supervisor = AgentSupervisor(
            database=self.session_controller.database,
            child_runtime_factory=lambda child_session_id: self._build_runtime_for_session(
                child_session_id,
                supervisor=None,
            ),
            event_sink=self._publish_agent_event,
        )
        self.set_permission_mode(config.permission_mode)

    def _publish_trace_event(self, event: TraceEvent) -> None:
        if self.active_session_id is not None:
            self._publish_trace_event_for_session(self.active_session_id, event)

    def _publish_trace_event_for_session(self, session_id: str, event: TraceEvent) -> None:
        tui_event = trace_event_to_tui_event(event)
        if tui_event is None:
            return
        # Child traces stay in SQLite. The TUI only receives permission prompts from a
        # child, while Supervisor emits the child lifecycle/status events below.
        if session_id != self.active_session_id and tui_event.type not in {"permission_requested", "permission_resolved"}:
            return
        self.event_stream.publish(replace(tui_event, session_id=session_id))

    def _publish_agent_event(self, payload: dict[str, object]) -> None:
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            return
        parent_session_id = payload.get("parent_session_id")
        self.event_stream.publish(
            TUIEvent(
                type=event_type,
                timestamp=now_iso(),
                session_id=parent_session_id if isinstance(parent_session_id, str) else self.active_session_id,
                payload=dict(payload),
            )
        )

    def _runtime(self) -> SessionRuntime:
        if self.active_session_id is None:
            raise RuntimeError("select or create a session before building a runtime")
        return self._build_runtime_for_session(self.active_session_id, supervisor=self.agent_supervisor)

    def _build_runtime_for_session(self, session_id: str, *, supervisor: AgentSupervisor | None) -> SessionRuntime:
        session = self.session_controller.store.sessions.get_session(session_id)
        opened = self.session_controller.service.open_session(session_id)
        raw_agent_type = session.metadata.get("agent_type")
        profile = get_agent_profile(raw_agent_type) if isinstance(raw_agent_type, str) else None
        write_scope = tuple(
            item for item in session.metadata.get("write_scope", ()) if isinstance(item, str)
        )
        mode = self.config.permission_mode if session_id == self.active_session_id else session.permission_mode
        mcp_registry = MCPToolRegistry.from_config(self.config.mcp_config) if self.config.mcp_config else None
        role_mcp_registry = mcp_registry if profile is None or profile.allows_mcp else None
        extra_specs = (
            {item.name: item for item in role_mcp_registry.list_specs()}
            if role_mcp_registry is not None
            else None
        )
        policy_checker = PolicyChecker.default(extra_tool_specs=extra_specs)
        metadata: dict[str, object] = {"agent_type": raw_agent_type} if raw_agent_type is not None else {}
        if profile is not None and profile.name == "general":
            metadata["write_scope"] = list(write_scope)
        policy_context = _policy_context_for_agent(mode, opened.project_path, profile).model_copy(
            update={"metadata": metadata}
        )
        session_broker = self._get_session_broker(session_id, mode=mode)

        def router_factory(trace):
            return ToolRouter(
                trace_logger=trace,
                policy_checker=policy_checker,
                policy_context=policy_context,
                external_tool_registry=role_mcp_registry,
                permission_broker=session_broker,
            )

        runtime_tool_registry_factory = None
        if profile is None and supervisor is not None:
            def runtime_tool_registry_factory(context: RuntimeToolContext):
                return build_agent_control_registry(supervisor, context)

        built_llm = build_codepilot_llm(
            fake_responses=self.config.fake_responses,
            model=session.current_model,
            model_config=list(self.config.model_config),
        )
        if (built_llm.provider, built_llm.model) != (session.provider, session.current_model):
            raise ValueError(
                "模型身份与 Session 快照不一致："
                f"{built_llm.provider}/{built_llm.model} != {session.provider}/{session.current_model}"
            )
        return SessionRuntime(
            self.session_controller.database,
            built_llm.client,
            router_factory,
            max_steps=self.config.max_steps,
            trace_hook=lambda event: self._publish_trace_event_for_session(session_id, event),
            capabilities=built_llm.capabilities,
            boundary_resolver=MultiAgentBoundaryResolver(profile, write_scope) if profile is not None else None,
            runtime_tool_registry_factory=runtime_tool_registry_factory,
        )

    def model_identity(self) -> tuple[str, str]:
        """返回纯解析得到的模型身份，不构造真实模型客户端。"""

        identity = resolve_codepilot_model_identity(
            fake_responses=self.config.fake_responses,
            model=self.config.model,
            model_config=list(self.config.model_config),
        )
        return identity.provider, identity.model

    def _session_broker(self) -> SessionPermissionBroker:
        if self.active_session_id is None:
            raise RuntimeError("select or create a session before accessing permission state")
        broker = self._get_session_broker(self.active_session_id, mode=self.config.permission_mode)
        self.session_permission_broker = broker
        self.permission_broker = broker
        return broker

    def _get_session_broker(self, session_id: str, *, mode: str) -> SessionPermissionBroker:
        if session_id == self.active_session_id:
            inner = self.mode_permission_broker
        else:
            inner = AutoApproveLocalWriteBroker(self.base_permission_broker) if mode == "accept_edits" else self.base_permission_broker
        broker = self._session_permission_brokers.get(session_id)
        if broker is None or broker.inner is not inner:
            broker = SessionPermissionBroker(self.session_controller.database, session_id, inner)
            self._session_permission_brokers[session_id] = broker
        return broker

    def _broker_for_request(self, request_id: str) -> SessionPermissionBroker:
        record = self.session_controller.store.permissions.get_permission_request(request_id)
        if record.session_id is None:
            return self._session_broker()
        if self.active_session_id is not None and record.session_id != self.active_session_id:
            child = self.session_controller.store.sessions.get_session(record.session_id)
            if child.parent_session_id != self.active_session_id:
                raise PermissionError("permission request does not belong to the active session tree")
        session = self.session_controller.store.sessions.get_session(record.session_id)
        return self._get_session_broker(record.session_id, mode=session.permission_mode)

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.config = replace(self.config, permission_mode=mode)
        self.mode_permission_broker = AutoApproveLocalWriteBroker(self.base_permission_broker) if mode == "accept_edits" else self.base_permission_broker
        if self.active_session_id is not None:
            self._session_permission_brokers.pop(self.active_session_id, None)
            self.session_permission_broker = SessionPermissionBroker(self.session_controller.database, self.active_session_id, self.mode_permission_broker)
            self._session_permission_brokers[self.active_session_id] = self.session_permission_broker
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
        except ToolExecutionUncertainError as exc:
            self._publish_worker_failure(
                session_id=self.active_session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                error=exc,
                status="recovery_required",
            )
        except Exception as exc:
            self._publish_worker_failure(
                session_id=self.active_session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                error=exc,
                status="interrupted",
            )
        finally:
            self.active_turn_id = None
            self._clear_current_worker()

    def _clear_current_worker(self) -> None:
        """只清理当前线程自己的槽位，禁止旧线程覆盖刚启动的新线程。"""

        with self._lock:
            if self._thread is threading.current_thread():
                self._thread = None

    def _publish_worker_failure(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        attempt_id: str | None,
        error: Exception,
        status: Literal["interrupted", "recovery_required"],
    ) -> None:
        """统一发布失败终态，让 App 能结束 running 状态并重新扫描恢复计划。"""

        if status == "recovery_required":
            # 未知副作用不是普通可重试错误；先发布专用事件，让 UI 直接进入 Recovery 流程。
            self.event_stream.publish(
                TUIEvent(
                    type="tool_execution_uncertain",
                    timestamp=now_iso(),
                    session_id=session_id,
                    payload={"error": str(error), "turn_id": turn_id, "attempt_id": attempt_id},
                )
            )
        self.event_stream.publish(
            TUIEvent(
                type="error",
                timestamp=now_iso(),
                session_id=session_id,
                payload={"error": str(error), "source": "agent_runtime", "turn_id": turn_id, "attempt_id": attempt_id},
            )
        )
        self.event_stream.publish(
            TUIEvent(
                type="run_finished",
                timestamp=now_iso(),
                session_id=session_id,
                payload={"status": status, "success": False, "turn_id": turn_id, "attempt_id": attempt_id},
            )
        )

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
            self._publish_worker_failure(
                session_id=self.active_session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                error=exc,
                status="recovery_required",
            )
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
        if self.active_session_id is not None:
            self.agent_supervisor.cancel_children(self.active_session_id)

    def restore_pending_permission(self, request_id: str):
        return self._broker_for_request(request_id).restore_pending_request(request_id)

    def resolve_permission(self, response: PermissionResponse) -> None:
        self._broker_for_request(response.request_id).resolve(response)

    def abort_pending_permission(self, request_id: str) -> None:
        self._broker_for_request(request_id).resolve(
            PermissionResponse(
                request_id=request_id,
                decision="deny",
                reason="aborted from TUI",
                responded_at=now_iso(),
            )
        )

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

