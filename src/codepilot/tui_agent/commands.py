from __future__ import annotations

from dataclasses import dataclass

from codepilot.tui_agent.diff_view import format_diff_summary
from codepilot.tui_agent.layout import format_side_status
from codepilot.tui_agent.models import AgentRunView, PermissionMode, ProjectContext, TUISession


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    output: str = ""
    exit_requested: bool = False
    new_task_requested: bool = False
    cancel_requested: bool = False
    permission_mode: PermissionMode | None = None
    open_copy_mode: bool = False
    copy_target: str | None = None
    export_transcript_requested: bool = False


def parse_slash_command(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    if not parts:
        return "", []
    return parts[0].lstrip("/").lower(), parts[1:]


def handle_command(
    text: str,
    *,
    view: AgentRunView,
    project: ProjectContext,
    session: TUISession,
    permission_mode: PermissionMode,
) -> CommandResult:
    command, args = parse_slash_command(text)
    if not command:
        return CommandResult(handled=False)
    if command == "help":
        return CommandResult(handled=True, output="/help /status /permissions /diff /report /trace /copy /export-transcript /new /cancel /exit")
    if command == "status":
        return CommandResult(handled=True, output=format_side_status(project, session, view, permission_mode))
    if command == "permissions":
        if not args:
            return CommandResult(handled=True, output=f"Permission mode: {permission_mode}")
        mode = args[0]
        if mode in {"manual", "read_only", "accept_edits", "unsafe_auto"}:
            return CommandResult(handled=True, output=f"Permission mode set to {mode}", permission_mode=mode)
        return CommandResult(handled=True, output=f"Unknown permission mode: {mode}")
    if command == "diff":
        return CommandResult(handled=True, output=format_diff_summary(view))
    if command == "report":
        return CommandResult(
            handled=True,
            output="\n".join(
                [
                    f"Report: {view.report_path or 'none'}",
                    f"Report JSON: {view.report_json_path or 'none'}",
                    f"Trace: {view.trace_path or 'none'}",
                ]
            ),
        )
    if command == "trace":
        return CommandResult(
            handled=True,
            output="\n".join(
                [
                    f"Trace: {view.trace_path or 'none'}",
                    f"Report: {view.report_path or 'none'}",
                    f"Report JSON: {view.report_json_path or 'none'}",
                ]
            ),
        )
    if command == "copy":
        target = args[0].lower() if args else "all"
        if target not in {"all", "last", "errors"}:
            return CommandResult(handled=True, output=f"Unknown copy target: {target}")
        return CommandResult(handled=True, output=f"Copy mode opened: {target}", open_copy_mode=True, copy_target=target)
    if command == "export-transcript":
        return CommandResult(handled=True, output="Transcript export requested", export_transcript_requested=True)
    if command == "new":
        return CommandResult(handled=True, output="Ready for new task", new_task_requested=True)
    if command == "cancel":
        return CommandResult(handled=True, output="Cancellation requested", cancel_requested=True)
    if command == "exit":
        return CommandResult(handled=True, output="Exit requested", exit_requested=True)
    return CommandResult(handled=True, output=f"Unknown command: {text.strip()}")
