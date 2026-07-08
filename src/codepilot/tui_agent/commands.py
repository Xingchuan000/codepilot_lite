from __future__ import annotations

from dataclasses import dataclass

from codepilot.tui_agent.diff_view import format_diff_summary
from codepilot.tui_agent.models import AgentRunView, PermissionMode, ProjectContext, TUISession


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    output: str = ""
    exit_requested: bool = False
    new_task_requested: bool = False
    cancel_requested: bool = False
    permission_mode: PermissionMode | None = None


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
        return CommandResult(handled=True, output="/help /status /permissions /diff /report /new /cancel /exit")
    if command == "status":
        return CommandResult(
            handled=True,
            output="\n".join(
                [
                    f"Project: {project.resolved_project}",
                    f"Git: {project.git_root or 'non-git'} ({project.git_dirty_status})",
                    f"Model: {session.model or 'default'}",
                    f"Permission: {permission_mode}",
                    f"Run: {view.status}",
                ]
            ),
        )
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
    if command == "new":
        return CommandResult(handled=True, output="Ready for new task", new_task_requested=True)
    if command == "cancel":
        return CommandResult(handled=True, output="Cancellation requested", cancel_requested=True)
    if command == "exit":
        return CommandResult(handled=True, output="Exit requested", exit_requested=True)
    return CommandResult(handled=True, output=f"Unknown command: {text.strip()}")

