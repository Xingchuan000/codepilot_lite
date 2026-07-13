from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from codepilot.agent.runner import build_codepilot_llm
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.session.models import BranchConfirmationRequired
from codepilot.session.runtime import SessionRuntime
from codepilot.trace.events import TraceEvent
from codepilot.tui_agent.event_stream import MemoryEventStream, trace_event_to_tui_event
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUIEvent, TUISession
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


def _policy_context_for_mode(mode: PermissionMode, repo: Path) -> PolicyContext:
    if mode == "read_only":
        return PolicyContext(repo=repo, mode="read_only", approved=False, interactive=True)
    if mode == "unsafe_auto":
        return PolicyContext(repo=repo, mode="danger", approved=True, interactive=True)
    return PolicyContext(repo=repo, mode="build", approved=False, interactive=True)


class TUIAgentRunner:
    """TUI 到 SessionRuntime 的单线程适配器，不拥有第二份 Session 状态。"""

    def __init__(self, *, project: ProjectContext, session: TUISession, session_store: SessionStore, event_stream: MemoryEventStream, permission_broker: PermissionBroker | None, config: TUIRunnerConfig) -> None:
        self.project = project
        self.session = session
        self.session_store = session_store
        self.event_stream = event_stream
        self.base_permission_broker = permission_broker or NonInteractiveBroker()
        self.permission_broker = self.base_permission_broker
        self.config = config
        self.active_session_id = session.session_id
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

        def router_factory(trace):
            return ToolRouter(
                trace_logger=trace,
                policy_checker=policy_checker,
                policy_context=_policy_context_for_mode(self.config.permission_mode, self.project.effective_repo_path),
                external_tool_registry=mcp_registry,
                permission_broker=self.permission_broker,
            )

        return SessionRuntime(self.session_store.database, llm, router_factory, max_steps=self.config.max_steps, trace_hook=self._publish_trace_event)

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.config = replace(self.config, permission_mode=mode)
        self.permission_broker = AutoApproveLocalWriteBroker(self.base_permission_broker) if mode == "accept_edits" else self.base_permission_broker

    def _run_task_worker(self, task: str) -> None:
        try:
            runtime = self._runtime()
            turn = runtime.submit_user_message(self.active_session_id, task)
            if isinstance(turn, BranchConfirmationRequired):
                self.event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), session_id=self.active_session_id, payload={"error": "branch changed", "old_branch": turn.old_branch, "new_branch": turn.new_branch}))
                return
            self.active_turn_id = turn.turn_id
            execution = runtime.run_turn(turn.turn_id, self.cancellation_token)
            self.event_stream.publish(TUIEvent(type="run_finished", timestamp=now_iso(), session_id=self.active_session_id, payload={"status": execution.result.status, "success": execution.result.success, "turn_id": turn.turn_id, "attempt_id": execution.result.trace_path}))
        except Exception as exc:
            self.event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), session_id=self.active_session_id, payload={"error": str(exc), "source": "agent_runtime"}))
        finally:
            with self._lock:
                self._thread = None

    def start_task(self, task: str) -> str:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("a task is already running")
            self.cancellation_token = CancellationToken()
            self._thread = threading.Thread(target=self._run_task_worker, args=(task,), daemon=True)
            self._thread.start()
            return f"turn-pending-{self.active_session_id[:12]}"

    def cancel_current(self) -> None:
        self.cancellation_token.cancel()
        self.permission_broker.cancel_all("cancelled from TUI")

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())
