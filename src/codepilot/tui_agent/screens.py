from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from codepilot.permissions import PermissionResponse
from codepilot.session.models import BranchConfirmationRequired, PendingTurnSubmission
from codepilot.session.recovery import RecoveryService
from codepilot.tui_agent.event_stream import MemoryEventStream
from codepilot.tui_agent.models import TUIEvent
from codepilot.tui_agent.runner import TUIAgentRunner
from codepilot.tui_agent.session_controller import now_iso
from codepilot.tui_agent.session_modals import format_branch_confirmation, format_recovery_modal


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

    def __init__(
        self,
        request: dict[str, Any],
        *,
        runner: TUIAgentRunner,
        event_stream: MemoryEventStream,
        recovery_service: RecoveryService,
    ) -> None:
        super().__init__()
        self.request = request
        self.runner = runner
        self.event_stream = event_stream
        self.recovery_service = recovery_service
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
            self.event_stream.publish(TUIEvent(type="error", timestamp=now_iso(), payload={"error": "permission request missing request_id"}))
            self.dismiss()
            return
        response = PermissionResponse(
            request_id=str(request_id),
            decision=decision,
            reason=reason,
            responded_at=now_iso(),
        )
        self.runner.resolve_permission(response)
        self.dismiss(response)

    def action_approve_once(self) -> None:
        self._resolve("approve_once", "approved once from TUI")

    def action_approve_session(self) -> None:
        if not self.can_approve_session:
            return
        self._resolve("approve_session", "approved for session from TUI")

    def action_deny(self) -> None:
        self._resolve("deny", "denied from TUI")

    def action_abort_pending(self) -> None:
        request_id = self.request.get("request_id")
        if not request_id:
            self.dismiss()
            return
        self.recovery_service.abort_pending_approval(str(request_id))
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

    def __init__(self, tool_call_id: str, *, recovery_service: RecoveryService) -> None:
        super().__init__()
        self.tool_call_id = tool_call_id
        self.call = recovery_service.store.tool_executions.get_tool_call(tool_call_id)
        self.result = recovery_service.reconcile_tool_call(tool_call_id)

    def compose(self) -> ComposeResult:
        yield Static(format_recovery_modal(self.call.tool_name, self.call.arguments, self.call.started_at, self.result))

    def action_mark_completed(self) -> None:
        self.dismiss("mark completed")

    def action_retry(self) -> None:
        self.dismiss("retry")

    def action_abort(self) -> None:
        self.dismiss("abort")

