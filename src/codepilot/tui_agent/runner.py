from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from codepilot.agent.loop import AgentRunResult, MinimalAgentLoop
from codepilot.agent.runner import build_codepilot_llm
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.report.generator import generate_report
from codepilot.router import ToolRouter
from codepilot.trace.events import TraceEvent
from codepilot.trace.logger import TraceLogger, make_run_id
from codepilot.tui_agent.event_stream import MemoryEventStream, trace_event_to_tui_event
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUIEvent, TUISession, TUISessionRunRef
from codepilot.tui_agent.permission_broker import AutoApproveLocalWriteBroker, PermissionBroker, NonInteractiveBroker
from codepilot.tui_agent.session_store import SessionStore, now_iso, task_preview


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
    auto_report: bool = True


RunnerFailureSource = Literal["runner_setup", "agent_runtime"]
RunnerWarningSource = Literal["report_generation", "session_persistence"]


def _policy_context_for_mode(mode: PermissionMode, repo: Path) -> PolicyContext:
    if mode == "read_only":
        return PolicyContext(repo=repo, mode="read_only", approved=False, interactive=True)
    if mode == "unsafe_auto":
        return PolicyContext(repo=repo, mode="danger", approved=True, interactive=True)
    return PolicyContext(repo=repo, mode="build", approved=False, interactive=True)


class TUIAgentRunner:
    def __init__(
        self,
        *,
        project: ProjectContext,
        session: TUISession,
        session_store: SessionStore,
        event_stream: MemoryEventStream,
        permission_broker: PermissionBroker | None,
        config: TUIRunnerConfig,
    ) -> None:
        self.project = project
        self.session = session
        self.session_store = session_store
        self.event_stream = event_stream
        self.base_permission_broker = permission_broker or NonInteractiveBroker()
        self.permission_broker = self.base_permission_broker
        self.config = config
        # 新生命周期接口使用稳定的 Session 身份；旧 UI 仍可通过 session 兼容访问。
        self.active_session_id = session.session_id
        self.active_turn_id: str | None = None
        self.cancellation_token = CancellationToken()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._current_run_id: str | None = None
        self.set_permission_mode(config.permission_mode)

    def _publish_trace_event(self, event: TraceEvent) -> None:
        tui_event = trace_event_to_tui_event(event)
        if tui_event is not None:
            self.event_stream.publish(tui_event)

    def _publish_trace_hook_error(self, error: Exception, event: TraceEvent) -> None:
        """把 Trace → TUI 适配失败发布为可见诊断，不中断 Agent 主流程。"""

        self.event_stream.publish(
            TUIEvent(
                type="error",
                timestamp=now_iso(),
                run_id=event.run_id,
                session_id=self.session.session_id,
                payload={
                    "error": str(error),
                    "source": "trace_record_hook",
                    "trace_event_type": event.event_type,
                    "trace_step": event.step,
                },
            )
        )

    def _publish_runner_diagnostic(
        self,
        *,
        run_id: str,
        source: RunnerFailureSource | RunnerWarningSource,
        error: Exception,
        fatal: bool,
    ) -> None:
        """把 Runner 编排层的诊断统一发布成同一种 TUI 事件。"""

        self.event_stream.publish(
            TUIEvent(
                type="error",
                timestamp=now_iso(),
                run_id=run_id,
                session_id=self.session.session_id,
                payload={
                    "error": str(error),
                    "source": source,
                    "fatal": fatal,
                },
            )
        )

    def _build_agent_loop(self, *, run_id: str, trace_logger: TraceLogger) -> MinimalAgentLoop:
        """把 MCP、Policy、Router、LLM 和 AgentLoop 的构建收拢到一个阶段。"""

        mcp_registry = MCPToolRegistry.from_config(self.config.mcp_config) if self.config.mcp_config else None
        policy_checker = (
            PolicyChecker.default(extra_tool_specs={item.name: item for item in mcp_registry.list_specs()})
            if mcp_registry
            else PolicyChecker.default()
        )
        router = ToolRouter.from_runs_dir(
            runs_dir=self.session.runs_dir,
            run_id=run_id,
            trace_logger=trace_logger,
            policy_checker=policy_checker,
            policy_context=_policy_context_for_mode(self.config.permission_mode, self.project.effective_repo_path),
            external_tool_registry=mcp_registry,
            permission_broker=self.permission_broker,
        )
        llm = build_codepilot_llm(fake_actions=self.config.fake_actions, model=self.config.model, model_config=list(self.config.model_config))
        return MinimalAgentLoop(
            llm=llm,
            router=router,
            max_steps=self.config.max_steps,
            prompt_extra_tool_specs=mcp_registry.list_exposed_specs() if mcp_registry else None,
            cancellation_token=self.cancellation_token,
        )

    def _generate_report_paths(self, *, result: AgentRunResult, run_id: str) -> tuple[str | None, str | None]:
        """报告只是派生产物，失败时只发 warning，不覆盖 Agent 结果。"""

        if not self.config.auto_report or result.trace_path is None:
            return None, None
        try:
            report_path_obj, _ = generate_report(Path(result.trace_path), overwrite=True, write_json=True)
        except Exception as exc:
            self._publish_runner_diagnostic(run_id=run_id, source="report_generation", error=exc, fatal=False)
            return None, None
        return str(report_path_obj), str(report_path_obj.with_suffix(".json"))

    def _persist_completed_run(
        self,
        *,
        task: str,
        run_id: str,
        result: AgentRunResult,
        report_path: str | None,
        report_json_path: str | None,
        started_at: str,
    ) -> None:
        """把成功运行索引进 Session；失败时只发 warning，不回退 Agent 结果。"""

        run_ref = TUISessionRunRef.from_outcome(
            run_id=run_id,
            task_preview=task_preview(task),
            outcome=result.outcome,
            trace_path=result.trace_path,
            report_path=report_path,
            report_json_path=report_json_path,
            started_at=started_at,
            ended_at=now_iso(),
        )
        try:
            self.session = self.session_store.append_run(self.session, run_ref)
        except Exception as exc:
            self._publish_runner_diagnostic(run_id=run_id, source="session_persistence", error=exc, fatal=False)

    def _publish_completed_run(
        self,
        *,
        run_id: str,
        result: AgentRunResult,
        report_path: str | None,
        report_json_path: str | None,
    ) -> None:
        """成功结束时只发布一次最终事件，内容直接来自 AgentRunResult。"""

        self.event_stream.publish(
            TUIEvent(
                type="run_finished",
                timestamp=now_iso(),
                run_id=run_id,
                session_id=self.session.session_id,
                payload={
                    "success": result.success,
                    "trace_path": result.trace_path,
                    "report_path": report_path,
                    "report_json_path": report_json_path,
                    **result.outcome.to_payload(),
                },
            )
        )

    def _finish_runner_failure(
        self,
        *,
        task: str,
        run_id: str,
        started_at: str,
        trace_logger: TraceLogger | None,
        source: RunnerFailureSource,
        error: Exception,
    ) -> None:
        """把 runner_setup / agent_runtime 这两类致命异常收束成统一的失败结束事件。"""

        trace_path = str(trace_logger.trace_path) if trace_logger is not None else str(self.session.runs_dir / run_id / "trace.jsonl")
        if trace_logger is not None and not trace_logger.terminal_recorded:
            trace_logger.record_run_end(
                success=False,
                summary="failed",
                metadata={
                    "status": "failed",
                    "failure_source": source,
                    "error": str(error),
                },
            )
        try:
            self.session = self.session_store.append_run(
                self.session,
                TUISessionRunRef(
                    run_id=run_id,
                    task_preview=task_preview(task),
                    status="failed",
                    trace_path=trace_path,
                    started_at=started_at,
                    ended_at=now_iso(),
                    completion_kind="runtime_failure",
                ),
            )
        except Exception as session_exc:
            self._publish_runner_diagnostic(run_id=run_id, source="session_persistence", error=session_exc, fatal=False)
        self._publish_runner_diagnostic(run_id=run_id, source=source, error=error, fatal=True)
        self.event_stream.publish(
            TUIEvent(
                type="run_finished",
                timestamp=now_iso(),
                run_id=run_id,
                session_id=self.session.session_id,
                payload={
                    "status": "failed",
                    "success": False,
                    "trace_path": trace_path,
                    "error": str(error),
                    "failure_source": source,
                    "completion_kind": "runtime_failure",
                    "assistant_stop_reason": None,
                },
            )
        )

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.config = replace(self.config, permission_mode=mode)
        self.permission_broker = (
            AutoApproveLocalWriteBroker(self.base_permission_broker)
            if mode == "accept_edits"
            else self.base_permission_broker
        )

    def _run_task_worker(self, task: str, run_id: str) -> None:
        started_at = now_iso()
        trace_logger: TraceLogger | None = None
        try:
            try:
                trace_logger = TraceLogger(
                    runs_dir=self.session.runs_dir,
                    run_id=run_id,
                    record_hook=self._publish_trace_event,
                    record_hook_error=self._publish_trace_hook_error,
                )
                agent_loop = self._build_agent_loop(run_id=run_id, trace_logger=trace_logger)
            except Exception as exc:
                self._finish_runner_failure(task=task, run_id=run_id, started_at=started_at, trace_logger=trace_logger, source="runner_setup", error=exc)
                return

            try:
                result = agent_loop.run(task=task, repo=self.project.effective_repo_path)
            except Exception as exc:
                self._finish_runner_failure(task=task, run_id=run_id, started_at=started_at, trace_logger=trace_logger, source="agent_runtime", error=exc)
                return

            report_path, report_json_path = self._generate_report_paths(result=result, run_id=run_id)
            self._persist_completed_run(
                task=task,
                run_id=run_id,
                result=result,
                report_path=report_path,
                report_json_path=report_json_path,
                started_at=started_at,
            )
            self._publish_completed_run(run_id=run_id, result=result, report_path=report_path, report_json_path=report_json_path)
        finally:
            with self._lock:
                self._current_run_id = None
                self._thread = None

    def start_task(self, task: str) -> str:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("a task is already running")
            self.cancellation_token = CancellationToken()
            run_id = make_run_id()
            self._current_run_id = run_id
            self.event_stream.publish(
                TUIEvent(
                    type="run_started",
                    timestamp=now_iso(),
                    run_id=run_id,
                    payload={"task": task, "trace_path": str(self.session.runs_dir / run_id / "trace.jsonl")},
                )
            )
            self.session_store.append_message(self.session, role="user", content=task, run_id=run_id)
            self._thread = threading.Thread(target=self._run_task_worker, args=(task, run_id), daemon=True)
            self._thread.start()
            return run_id

    def cancel_current(self) -> None:
        self.cancellation_token.cancel()
        self.permission_broker.cancel_all("cancelled from TUI")

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())
