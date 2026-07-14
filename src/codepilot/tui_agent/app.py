from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from codepilot.permissions import PermissionResponse
from codepilot.session.database import SessionDatabase
from codepilot.session.compaction import CompactionService
from codepilot.session.exporter import SessionExporter
from codepilot.session.models import BranchConfirmationRequired, PendingTurnSubmission
from codepilot.session.paths import resolve_session_paths
from codepilot.session.recovery import RecoveryPlan, RecoveryService
from codepilot.session.service import SessionService
from codepilot.tui_agent.commands import handle_command, parse_slash_command
from codepilot.tui_agent.config import merge_config
from codepilot.tui_agent.event_reducer import EventReducer
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.layout import format_header, format_side_status, format_transcript_item, format_transcript_plain
from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUIEvent
from codepilot.tui_agent.permission_broker import BlockingTUIBroker
from codepilot.tui_agent.project_resolver import resolve_project
from codepilot.tui_agent.runner import TUIAgentRunner, TUIRunnerConfig
from codepilot.tui_agent.session_modals import format_branch_confirmation, format_recovery_modal
from codepilot.tui_agent.session_hydrator import hydrate_session_view
from codepilot.tui_agent.session_picker import SessionPicker, SessionPickerResult, SessionPickerScreen
from codepilot.tui_agent.session_store import SessionStore, now_iso


SESSION_LIFECYCLE_COMMANDS = {"sessions", "switch", "new", "archive", "unarchive", "move", "compact", "export-session"}


def _load_textual():
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Horizontal, Vertical, VerticalScroll
        from textual.screen import ModalScreen
        from textual.widgets import Footer, Header, Input, Static, TextArea
    except ImportError as exc:
        raise RuntimeError("Textual is not installed. Install textual or use codepilot agent-run.") from exc
    return App, ComposeResult, Horizontal, Vertical, VerticalScroll, Footer, Header, Input, Static, TextArea, ModalScreen, Binding


def create_tui_agent_app(
    *,
    project: str | Path | None = None,
    model: str | None = None,
    model_config: list[str] | None = None,
    permission_mode: PermissionMode | None = None,
    mcp_config: str | Path | None = None,
    runs_dir: str | Path | None = None,
    fake_actions: str | Path | None = None,
    max_steps: int | None = None,
    session_database: SessionDatabase | None = None,
):
    App, ComposeResult, Horizontal, Vertical, VerticalScroll, Footer, Header, Input, Static, TextArea, ModalScreen, Binding = _load_textual()

    project_context = resolve_project(project)
    merged = merge_config(
        cli_model=model,
        cli_permission_mode=permission_mode,
        cli_runs_dir=runs_dir,
        cli_mcp_config=mcp_config,
        cli_max_steps=max_steps,
        project=project_context,
    )
    # TUI 的所有组件共享这一份用户级数据库；项目路径不再参与数据库位置计算。
    paths = resolve_session_paths(session_database.path.parent if session_database is not None else None)
    database = session_database or SessionDatabase(paths.database_path)
    database.initialize()
    service = SessionService(database, paths)
    recovery_service = RecoveryService(database)
    session_store = SessionStore(project_context, database, paths)
    session = None
    event_stream = MemoryEventStream()
    broker = BlockingTUIBroker()
    runner = TUIAgentRunner(
        project=project_context,
        session=session,
        session_store=session_store,
        event_stream=event_stream,
        permission_broker=broker,
        config=TUIRunnerConfig(
            model=merged.model,
            model_config=tuple(model_config or []),
            permission_mode=merged.permission_mode,
            fake_actions=fake_actions,
            mcp_config=merged.mcp_config,
            max_steps=merged.max_steps,
            auto_report=merged.auto_report,
        ),
    )
    reducer = EventReducer()
    session_picker = SessionPicker(service)
    session_exporter = SessionExporter(database, paths)
    compaction_service = CompactionService(database)

    class SelectableStatic(Static):
        can_focus = True

        def selection_updated(self, selection) -> None:
            super().selection_updated(selection)
            if selection is None:
                return
            selected_text = self.screen.get_selected_text() if hasattr(self.screen, "get_selected_text") else ""
            if selected_text:
                self.app.copy_to_clipboard(selected_text)

    class TranscriptCopyScreen(ModalScreen[None]):
        BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("ctrl+a", "select_all", "Select all")]

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

        def compose(self) -> ComposeResult:
            yield TextArea(self.text, read_only=True, id="copy-text")

        def on_mount(self) -> None:
            self.query_one("#copy-text", TextArea).focus()

        def action_select_all(self) -> None:
            self.query_one("#copy-text", TextArea).select_all()

        def action_dismiss(self) -> None:
            self.dismiss()

    class PermissionModal(ModalScreen[PermissionResponse | None]):
        BINDINGS = [
            Binding("y", "approve_once", "Approve once"),
            Binding("s", "approve_session", "Approve for session"),
            Binding("n", "deny", "Deny"),
            Binding("a", "abort_pending", "Abort pending"),
            Binding("escape", "deny", "Deny"),
        ]

        def __init__(self, request: dict[str, Any]) -> None:
            super().__init__()
            self.request = request
            self.can_approve_session = bool(self.request.get("scope_key"))

        def compose(self) -> ComposeResult:
            actions = "Actions: Y = once, N/Esc = deny"
            if self.can_approve_session:
                actions = "Actions: Y = once, S = session, N/Esc = deny"
            yield Static(
                "\n".join(
                    [
                        f"Agent wants to run/edit: {self.request.get('tool_name')}",
                        f"Reason: {self.request.get('reason')}",
                        f"Risk: {self.request.get('risk')} / {self.request.get('side_effect')}",
                        f"Matched rule: {self.request.get('matched_rule')}",
                        f"Arguments: {self.request.get('arguments_preview')}",
                        f"Session scope: {self.request.get('scope_key') or '(none)'}",
                        actions,
                    ]
                )
            )

        def _resolve(self, decision: str, reason: str) -> None:
            request_id = self.request.get("request_id")
            if not request_id:
                event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "permission request missing request_id"}))
                self.dismiss()
                return
            response = PermissionResponse(
                request_id=str(request_id),
                decision=decision,
                reason=reason,
                responded_at=now_iso(),
            )
            runner.resolve_permission(response)
            self.dismiss(response)

        def action_approve_once(self) -> None:
            self._resolve("approve_once", "approved once from TUI")

        def action_approve_session(self) -> None:
            if not self.can_approve_session:
                return
            # 只有 scope_key 存在时才允许升级为会话级授权，避免把一次性请求误放大。
            self._resolve("approve_session", "approved for session from TUI")

        def action_deny(self) -> None:
            self._resolve("deny", "denied from TUI")

        def action_abort_pending(self) -> None:
            request_id = self.request.get("request_id")
            if not request_id:
                self.dismiss()
                return
            recovery_service.abort_pending_approval(str(request_id))
            self.dismiss()

    class BranchConfirmationModal(ModalScreen[bool]):
        """显示可恢复的分支变化确认；取消不会调用任何数据库写入方法。"""

        BINDINGS = [Binding("y", "confirm", "Continue"), Binding("n", "cancel", "Cancel"), Binding("escape", "cancel", "Cancel")]

        def __init__(self, pending: PendingTurnSubmission) -> None:
            super().__init__()
            self.pending = pending

        def compose(self) -> ComposeResult:
            yield Static(
                format_branch_confirmation(
                    BranchConfirmationRequired(
                        session_id=self.pending.session_id,
                        old_branch=self.pending.old_branch,
                        new_branch=self.pending.new_branch,
                    )
                )
            )

        def action_confirm(self) -> None:
            self.dismiss(True)

        def action_cancel(self) -> None:
            self.dismiss(False)

    class RecoveryModal(ModalScreen[str]):
        """仅对自动对账无法确认的副作用显示人工恢复动作。"""

        BINDINGS = [
            Binding("m", "mark_completed", "Mark completed"),
            Binding("r", "retry", "Retry"),
            Binding("a", "abort", "Abort"),
        ]

        def __init__(self, tool_call_id: str) -> None:
            super().__init__()
            self.tool_call_id = tool_call_id
            self.call = recovery_service.store.get_tool_call(tool_call_id)
            self.result = recovery_service.reconcile_tool_call(tool_call_id)

        def compose(self) -> ComposeResult:
            yield Static(format_recovery_modal(self.call.tool_name, self.call.arguments, self.call.started_at, self.result))

        def action_mark_completed(self) -> None:
            self.dismiss("mark completed")

        def action_retry(self) -> None:
            self.dismiss("retry")

        def action_abort(self) -> None:
            self.dismiss("abort")

    class CodePilotTUIAgentApp(App):
        permission_mode = merged.permission_mode
        CSS = """
        #body {
            height: 1fr;
        }
        #transcript {
            width: 1fr;
            min-width: 0;
        }
        #side-status {
            width: 40;
            min-width: 40;
        }
        """
        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("ctrl+y", "copy_transcript", "Copy transcript"),
            Binding("ctrl+o", "open_copy_screen", "Copy mode"),
        ]

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.session = session
            self.runner = runner
            self._reducer = reducer
            self._event_stream = event_stream
            self._project_context = project_context
            self._session_store = session_store
            self._session_service = service
            self._recovery_service = recovery_service
            self._compaction_service = compaction_service
            self._session_database = database
            self._session_paths = paths
            self._new_session_project_context = project_context
            self._session_read_only = True
            self._shown_permission_request_ids: set[str] = set()
            self._shown_branch_confirmations: set[tuple[str, str | None, str | None, str]] = set()
            self._recovery_scan_pending = False
            self._rendered_transcript_ids: set[str] = set()
            self._auto_scroll = True
            self._last_top_status_text: str | None = None
            self._last_side_status_text: str | None = None
            self.session_picker = session_picker
            self.session_exporter = session_exporter

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="root"):
                yield SelectableStatic(id="top-status")
                with Horizontal(id="body"):
                    with VerticalScroll(id="transcript"):
                        pass
                    yield SelectableStatic(id="side-status")
                yield Input(placeholder="输入任务或 /help", id="task-input")
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(0.2, self._drain_events)
            # 未选择 Session 时没有可写事实边界，输入必须禁用；新建和打开操作由 Picker
            # 完成，不能依赖提交后才报错来表达只读状态。
            self.query_one("#task-input", Input).disabled = True
            self._refresh()
            self.push_screen(SessionPickerScreen(self.session_picker), self._handle_session_picker_result)

        def _handle_session_picker_result(self, result: SessionPickerResult | None) -> None:
            """处理 Picker 的显式结果；仅 `new` 分支创建 Session。"""

            if result is None or result.action == "cancel":
                return
            if result.action == "new":
                self._create_new_session()
                return
            if result.session_id is None:
                raise ValueError("session picker open result requires session_id")
            self._activate_session(result.session_id)

        def _create_new_session(self) -> None:
            self._project_context = self._new_session_project_context
            self._session_store = SessionStore(self._project_context, self._session_database, self._session_paths)
            created = self._session_store.create_session(
                model=merged.model,
                permission_mode=merged.permission_mode,
                metadata={
                    "config_source": merged.source,
                    "max_steps": merged.max_steps,
                    "auto_report": merged.auto_report,
                    "mcp_enabled": merged.mcp_config is not None,
                },
            )
            self._bind_session(created, read_only=False)

        def _activate_session(self, session_id: str) -> None:
            opened = self._session_service.open_session(session_id)
            recovery_plan = (
                self._recovery_service.recover_session(session_id)
                if opened.project_exists and opened.session.status == "active"
                else self._recovery_service.inspect_session(session_id)
            )
            if opened.project_exists:
                project_for_session = resolve_project(opened.project_path)
            else:
                # 路径缺失是计划内的只读状态：保留原路径用于展示，但绝不构造可执行仓库。
                project_for_session = ProjectContext(
                    schema_version=project_context.schema_version,
                    project_path=opened.project_path,
                    resolved_project=opened.project_path,
                    git_root=None,
                    is_git_repo=False,
                    git_dirty_status="missing",
                    workspace_root=opened.project_path,
                    effective_repo_path=opened.project_path,
                    default_runs_dir=opened.project_path / "runs",
                    warnings=("project_path_missing",),
                )
            self._project_context = project_for_session
            self._session_store = SessionStore(project_for_session, self._session_database, self._session_paths)
            self._bind_session(
                self._session_store.load_session(session_id),
                read_only=opened.read_only or opened.session.status != "active",
            )
            self._handle_recovery_plan(recovery_plan)

        def _apply_command_result(self, result) -> str | None:
            """统一应用命令结果，避免在输入处理里分散状态切换逻辑。"""

            if self.runner.is_running() and _changes_session_lifecycle(result):
                return "当前 Turn 正在运行，请先完成或 /cancel"

            if result.permission_mode is not None:
                self.permission_mode = result.permission_mode
                self.runner.set_permission_mode(result.permission_mode)
                self.session = self._session_store.update_session(self.session, permission_mode=result.permission_mode)
                self.runner.session = self.session
            if result.rename_title is not None:
                self.session = self._session_store.update_session(self.session, title=result.rename_title)
                self.runner.session = self.session
            if result.new_task_requested:
                self._create_new_session()
            if result.open_session_picker:
                self.push_screen(SessionPickerScreen(self.session_picker), self._handle_session_picker_result)
            if result.switch_session_id is not None:
                self._activate_session(result.switch_session_id)
            if result.archive_current_session:
                self.session = self._session_service.archive_session(self.session.session_id)
                self._bind_session(self._session_store.load_session(self.session.session_id), read_only=True)
                self.push_screen(SessionPickerScreen(self.session_picker), self._handle_session_picker_result)
            if result.unarchive_session_id is not None:
                self._session_service.unarchive_session(result.unarchive_session_id)
            if result.compact_requested:
                self._compaction_service.compact(self.session.session_id, force=True)
            if result.export_session_requested:
                if self.session_exporter is None:
                    raise RuntimeError("Session export requires a SQLite Session database")
                exported = self.session_exporter.export(self.session.session_id, result.export_target)
                return f"Session exported: {exported}"
            if result.export_transcript_requested:
                return result.output
            if result.next_new_session_project is not None:
                self._new_session_project_context = resolve_project(result.next_new_session_project)
            return None

        def _handle_recovery_plan(self, plan: RecoveryPlan) -> None:
            """自动恢复只执行已确认安全的 Attempt；未知副作用必须先弹窗。"""

            if plan.pending_approval_request_ids:
                request = self.runner.restore_pending_permission(plan.pending_approval_request_ids[0])
                self._reducer.view = replace(self._reducer.view, status="waiting_permission")
                self._shown_permission_request_ids.add(request.request_id)
                self.push_screen(PermissionModal(request.__dict__), self._on_restored_permission_resolved)
                return
            if plan.resumable_attempt_ids:
                attempt = self._recovery_service.store.get_attempt(plan.resumable_attempt_ids[0])
                self.runner.resume_turn(attempt.turn_id, attempt.attempt_id)
                self._reducer.view = replace(self._reducer.view, status="running")
                return
            if plan.unresolved_tool_call_ids:
                tool_call_id = plan.unresolved_tool_call_ids[0]
                self.push_screen(
                    RecoveryModal(tool_call_id),
                    lambda decision, tool_call_id=tool_call_id: self._resolve_recovery(tool_call_id, decision),
                )

        def _on_restored_permission_resolved(self, response: PermissionResponse | None) -> None:
            """重启审批结束后创建新 Attempt；旧 Worker 不存在，绝不尝试唤醒旧队列。"""

            if response is None:
                return
            attempt = self._recovery_service.resume_after_permission(response.request_id)
            if attempt is not None:
                self.runner.resume_turn(attempt.turn_id, attempt.attempt_id)
                self._reducer.view = replace(self._reducer.view, status="running")
            else:
                self._reducer.view = replace(self._reducer.view, status="cancelled")
            self._refresh()

        def _resolve_recovery(self, tool_call_id: str, decision: str | None) -> None:
            if decision is None:
                return
            attempt = self._recovery_service.resolve_unknown(tool_call_id, decision)
            if attempt is not None:
                self.runner.resume_turn(attempt.turn_id, attempt.attempt_id)
                self._reducer.view = replace(self._reducer.view, status="running")
                self._refresh()
                return
            self._handle_recovery_plan(self._recovery_service.inspect_session(self.session.session_id))

        def _bind_session(self, selected_session, *, read_only: bool) -> None:
            """让 App、Runner 和输入状态同时切换到同一个 SQLite Session。"""

            self.session = selected_session
            self.permission_mode = selected_session.permission_mode
            self._session_read_only = read_only
            transcript_panel = self.query_one("#transcript", VerticalScroll)
            if hasattr(transcript_panel, "clear"):
                transcript_panel.clear()
            elif hasattr(transcript_panel, "mounted"):
                transcript_panel.mounted.clear()
            self.runner.project = self._project_context
            self.runner.session_store = self._session_store
            self.runner.session = selected_session
            self.runner.active_session_id = selected_session.session_id
            self.runner.config = replace(self.runner.config, model=selected_session.current_model)
            self.runner.set_permission_mode(selected_session.permission_mode)
            self.query_one("#task-input", Input).disabled = read_only
            self._last_top_status_text = None
            self._last_side_status_text = None
            hydrated = hydrate_session_view(self._session_store, selected_session.session_id)
            self._reducer.view = replace(
                self._reducer.view,
                run_id=hydrated.run_id,
                task=hydrated.task,
                status=hydrated.status,
                current_step=hydrated.current_step,
                current_tool=hydrated.current_tool,
                active_tool=hydrated.active_tool,
                last_assistant_message=hydrated.last_assistant_message,
                last_tool_output=hydrated.last_tool_output,
                transcript=hydrated.transcript,
                timeline=hydrated.timeline,
                changed_files=hydrated.changed_files,
                test_status=hydrated.test_status,
                permission_requests=hydrated.permission_requests,
                report_path=hydrated.report_path,
                report_json_path=hydrated.report_json_path,
                trace_path=hydrated.trace_path,
                warnings=hydrated.warnings,
            )
            self._rendered_transcript_ids = {item.id for item in hydrated.transcript}
            self._refresh()

        def _top_status_text(self) -> str:
            return format_header(self._project_context, self.session, self._reducer.view, self.permission_mode).replace("\n", " | ")

        def _refresh_top_status(self) -> None:
            text = self._top_status_text()
            if text == self._last_top_status_text:
                return
            self.query_one("#top-status", SelectableStatic).update(text)
            self._last_top_status_text = text

        def _refresh_side_status(self) -> None:
            text = format_side_status(self._project_context, self.session, self._reducer.view, self.permission_mode)
            if text == self._last_side_status_text:
                return
            self.query_one("#side-status", SelectableStatic).update(text)
            self._last_side_status_text = text

        def _append_new_transcript_items(self) -> None:
            panel = self.query_one("#transcript", VerticalScroll)
            should_auto_scroll = self._auto_scroll and getattr(panel, "is_vertical_scroll_end", True)
            for item in self._reducer.view.transcript:
                if item.id in self._rendered_transcript_ids:
                    continue
                panel.mount(SelectableStatic(format_transcript_item(item), markup=False, id=f"msg-{item.id}"))
                self._rendered_transcript_ids.add(item.id)
            if should_auto_scroll and hasattr(panel, "scroll_end"):
                panel.scroll_end(animate=False)

        def _refresh(self) -> None:
            self._append_new_transcript_items()
            self._refresh_top_status()
            self._refresh_side_status()

        def _transcript_items_for_target(self, target: str | None) -> tuple:
            items = self._reducer.view.transcript
            if target == "last":
                for item in reversed(items):
                    if item.kind in {"assistant_raw", "assistant_plan", "assistant_action", "tool_result", "final_summary"}:
                        return (item,)
                return ()
            if target == "errors":
                return tuple(item for item in items if item.kind == "error" or (item.kind == "tool_result" and item.status == "failed"))
            return items

        def _transcript_copy_text(self, target: str | None = None) -> str:
            items = self._transcript_items_for_target(target)
            if not items:
                if target == "errors":
                    return "No error transcript items yet."
                if target == "last":
                    return "No assistant or tool output yet."
                return "Transcript is empty."
            return format_transcript_plain(items)

        def _publish_command_output(self, command: str, output: str) -> None:
            if output:
                self._event_stream.publish(TUIEvent(type="command_output", timestamp=now_iso(), payload={"command": command, "output": output}))

        def copy_to_clipboard(self, text: str) -> None:
            super().copy_to_clipboard(text)
            if sys.platform == "darwin" and shutil.which("pbcopy") is not None:
                subprocess.run(["pbcopy"], input=text, text=True, check=False)

        def _drain_events(self) -> None:
            events = self._event_stream.drain()
            for event in events:
                self._reducer.reduce(event)
                if event.type == "run_finished":
                    self._recovery_scan_pending = True
            pending_permission_ids = {
                request.request_id
                for request in self._reducer.view.permission_requests
                if request.status == "pending"
            }
            for event in events:
                if event.type != "permission_requested":
                    continue
                request_id = event.payload.get("request_id")
                if not request_id:
                    continue
                request_id = str(request_id)
                if request_id in self._shown_permission_request_ids:
                    continue
                if request_id not in pending_permission_ids:
                    continue
                self._shown_permission_request_ids.add(request_id)
                self.push_screen(PermissionModal(event.payload))
            for event in events:
                if event.type != "branch_confirmation_required" or event.session_id is None:
                    continue
                pending = PendingTurnSubmission(
                    session_id=event.session_id,
                    text=str(event.payload.get("text") or ""),
                    old_branch=event.payload.get("old_branch"),
                    new_branch=event.payload.get("new_branch"),
                )
                key = (pending.session_id, pending.old_branch, pending.new_branch, pending.text)
                if key in self._shown_branch_confirmations:
                    continue
                self._shown_branch_confirmations.add(key)
                self.push_screen(
                    BranchConfirmationModal(pending),
                    lambda confirmed, pending=pending: self._resolve_branch_confirmation(pending, bool(confirmed)),
                )
            if self._recovery_scan_pending and self.session is not None and not self.runner.is_running():
                self._recovery_scan_pending = False
                self._handle_recovery_plan(self._recovery_service.recover_session(self.session.session_id))
            self._refresh()

        def _resolve_branch_confirmation(self, pending: PendingTurnSubmission, confirmed: bool) -> None:
            """确认时恢复原提交；取消只结束等待态，因此 SQLite 保持零写入。"""

            self._shown_branch_confirmations.discard((pending.session_id, pending.old_branch, pending.new_branch, pending.text))
            if not confirmed:
                self._reducer.view = replace(self._reducer.view, status="idle")
                self._refresh()
                return
            run_id = self.runner.resume_after_branch_confirmation(pending)
            self._reducer.view = replace(self._reducer.view, run_id=run_id, task=pending.text, status="running")
            self._refresh()

        def action_copy_transcript(self) -> None:
            self.copy_to_clipboard(self._transcript_copy_text("all"))

        def action_open_copy_screen(self) -> None:
            self.push_screen(TranscriptCopyScreen(self._transcript_copy_text("all")))

        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return
            if self.session is None:
                if text == "/exit":
                    self.exit()
                    return
                if text.startswith("/"):
                    command, _ = parse_slash_command(text)
                    if command == "sessions":
                        self.push_screen(SessionPickerScreen(self.session_picker), self._handle_session_picker_result)
                        return
                    if command == "new":
                        self._create_new_session()
                        return
                self._event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "请先在 Session Picker 中打开或新建 Session"}))
                self._drain_events()
                return
            if self._session_read_only and not text.startswith("/"):
                self._event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "项目路径不存在或 Session 已归档，当前 Session 只读"}))
                self._drain_events()
                return
            if text.startswith("/"):
                command, _ = parse_slash_command(text)
                if runner.is_running() and command in SESSION_LIFECYCLE_COMMANDS:
                    self._event_stream.publish(
                        TUIEvent(type="error", timestamp=now_iso(), payload={"error": "当前 Turn 正在运行，请先完成或 /cancel"})
                    )
                    self._drain_events()
                    return
                result = handle_command(
                    text,
                    view=self._reducer.view,
                    project=self._project_context,
                    session=self.session,
                    permission_mode=self.permission_mode,
                )
                if result.cancel_requested:
                    self.runner.cancel_current()
                if result.exit_requested:
                    self.exit()
                    return
                if result.open_copy_mode:
                    self.push_screen(TranscriptCopyScreen(self._transcript_copy_text(result.copy_target)))
                output = self._apply_command_result(result)
                self._publish_command_output(text, output or result.output)
                self._drain_events()
                self.query_one("#task-input", Input).value = ""
                return
            if runner.is_running():
                self._event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "已有任务正在运行"}))
                self._drain_events()
                return
            try:
                run_id = runner.start_task(text)
                self._reducer.view = replace(self._reducer.view, run_id=run_id, task=text, status="running")
                self.query_one("#task-input", Input).value = ""
                self._drain_events()
            except RuntimeError as exc:
                self._event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": str(exc)}))
                self._drain_events()

    return CodePilotTUIAgentApp()


def _changes_session_lifecycle(result: Any) -> bool:
    """防止未来非 Input 入口绕过运行中 Session 生命周期锁。"""

    return bool(
        result.new_task_requested
        or result.open_session_picker
        or result.switch_session_id is not None
        or result.archive_current_session
        or result.unarchive_session_id is not None
        or result.next_new_session_project is not None
        or result.compact_requested
    )
