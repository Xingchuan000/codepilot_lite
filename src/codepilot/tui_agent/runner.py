from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.agent.runner import build_codepilot_llm
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.report.generator import generate_report
from codepilot.router import ToolRouter
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
        self.cancellation_token = CancellationToken()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._current_run_id: str | None = None
        self.set_permission_mode(config.permission_mode)

    def _publish_trace_event(self, event: Any) -> None:
        self.event_stream.publish(trace_event_to_tui_event(event))

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.config = replace(self.config, permission_mode=mode)
        self.permission_broker = (
            AutoApproveLocalWriteBroker(self.base_permission_broker)
            if mode == "accept_edits"
            else self.base_permission_broker
        )

    def _run_task_worker(self, task: str, run_id: str) -> None:
        started_at = now_iso()
        trace_logger = TraceLogger(runs_dir=self.session.runs_dir, run_id=run_id, record_hook=self._publish_trace_event)
        mcp_registry = MCPToolRegistry.from_config(self.config.mcp_config) if self.config.mcp_config else None
        router = ToolRouter.from_runs_dir(
            runs_dir=self.session.runs_dir,
            run_id=run_id,
            trace_logger=trace_logger,
            policy_checker=PolicyChecker.default(extra_tool_specs={item.name: item for item in mcp_registry.list_specs()}) if mcp_registry else PolicyChecker.default(),
            policy_context=_policy_context_for_mode(self.config.permission_mode, self.project.effective_repo_path),
            external_tool_registry=mcp_registry,
            permission_broker=self.permission_broker,
        )
        llm = build_codepilot_llm(
            fake_actions=self.config.fake_actions,
            model=self.config.model,
            model_config=list(self.config.model_config),
        )
        try:
            result = MinimalAgentLoop(
                llm=llm,
                router=router,
                max_steps=self.config.max_steps,
                prompt_extra_tool_specs=mcp_registry.list_exposed_specs() if mcp_registry else None,
                cancellation_token=self.cancellation_token,
            ).run(task=task, repo=self.project.effective_repo_path)
            report_path = None
            report_json_path = None
            if self.config.auto_report and result.trace_path is not None:
                report_path_obj, _ = generate_report(Path(result.trace_path), overwrite=True, write_json=True)
                report_path = str(report_path_obj)
                report_json_path = str(report_path_obj.with_suffix(".json"))
            self.session = self.session_store.append_run(
                self.session,
                TUISessionRunRef(
                    run_id=run_id,
                    task_preview=task_preview(task),
                    status=result.status,
                    trace_path=result.trace_path,
                    report_path=report_path,
                    report_json_path=report_json_path,
                    started_at=started_at,
                    ended_at=now_iso(),
                    changed_files=tuple(result.changed_files),
                    tests=result.last_test_status,
                ),
            )
            self.event_stream.publish(
                TUIEvent(
                    type="run_finished",
                    timestamp=now_iso(),
                    run_id=run_id,
                    session_id=self.session.session_id,
                    payload={
                        "status": result.status,
                        "success": result.success,
                        "trace_path": result.trace_path,
                        "report_path": report_path,
                        "report_json_path": report_json_path,
                        "changed_files": list(result.changed_files),
                        "test_status": result.last_test_status,
                    },
                )
            )
        except Exception as exc:
            trace_logger.record_run_end(success=False, summary="llm_error", metadata={"status": "llm_error", "error": str(exc)})
            self.session = self.session_store.append_run(
                self.session,
                TUISessionRunRef(
                    run_id=run_id,
                    task_preview=task_preview(task),
                    status="llm_error",
                    trace_path=str(trace_logger.trace_path),
                    started_at=started_at,
                    ended_at=now_iso(),
                ),
            )
            self.event_stream.publish(
                TUIEvent(
                    type="run_finished",
                    timestamp=now_iso(),
                    run_id=run_id,
                    session_id=self.session.session_id,
                    payload={"status": "llm_error", "trace_path": str(trace_logger.trace_path), "error": str(exc)},
                )
            )
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
                    payload={"metadata": {"task": task}, "trace_path": str(self.session.runs_dir / run_id / "trace.jsonl")},
                )
            )
            self.session_store.append_message(self.session, role="user", content=task, run_id=run_id)
            self._thread = threading.Thread(target=self._run_task_worker, args=(task, run_id), daemon=True)
            self._thread.start()
            return run_id

    def cancel_current(self) -> None:
        self.cancellation_token.cancel()
        cancel_all = getattr(self.permission_broker, "cancel_all", None)
        if callable(cancel_all):
            cancel_all("cancelled from TUI")

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())
