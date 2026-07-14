from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codepilot.tui_agent.diff_view import format_diff_summary
from codepilot.tui_agent.layout import format_side_status
from codepilot.session.models import SessionRecord
from codepilot.tui_agent.models import AgentRunView, PermissionMode, ProjectContext


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
    switch_session_id: str | None = None
    rename_title: str | None = None
    open_session_picker: bool = False
    archive_current_session: bool = False
    unarchive_session_id: str | None = None
    compact_requested: bool = False
    export_session_requested: bool = False
    export_target: Path | None = None
    next_new_session_project: Path | None = None
    model_name: str | None = None

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
    session: SessionRecord,
    permission_mode: PermissionMode,
) -> CommandResult:
    command, args = parse_slash_command(text)
    if not command:
        return CommandResult(handled=False)
    if command == "help":
        return CommandResult(handled=True, output="/help /sessions /new /switch <session-id> /rename <title> /model <model-name> /archive /unarchive <session-id> /compact /export-session [path] /cancel /exit")
    if command == "sessions":
        return CommandResult(handled=True, output="Session picker requested", open_session_picker=True)
    if command == "switch":
        return CommandResult(handled=True, output="Session switch requested" if args else "Usage: /switch <session-id>", switch_session_id=args[0] if args else None)
    if command == "rename":
        title = " ".join(args).strip()
        return CommandResult(handled=True, output="Session rename requested" if title else "Usage: /rename <title>", rename_title=title or None)
    if command == "model":
        model = " ".join(args).strip()
        return CommandResult(handled=True, output="Session model change requested" if model else "Usage: /model <model-name>", model_name=model or None)
    if command == "archive":
        return CommandResult(handled=True, output="Session archive requested", archive_current_session=True)
    if command == "unarchive":
        return CommandResult(handled=True, output="Session unarchive requested", unarchive_session_id=args[0] if args else None)
    if command == "compact":
        return CommandResult(handled=True, output="Session compaction requested", compact_requested=True)
    if command == "export-session":
        return CommandResult(
            handled=True,
            output="Session export requested",
            export_session_requested=True,
            export_target=Path(" ".join(args)).expanduser() if args else None,
        )
    if command == "status":
        return CommandResult(handled=True, output=format_side_status(project, session, view, permission_mode))
    if command == "permissions":
        if not args:
            return CommandResult(
                handled=True,
                output="\n".join(
                    [
                        f"Permission mode: {permission_mode}",
                        "manual 表示写操作和有风险的 shell/MCP 动作仍然需要确认。",
                        "只读读取工具可以自动执行，不需要每次都弹确认。",
                    ]
                ),
            )
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
    if command == "move":
        if not args:
            return CommandResult(handled=True, output="Usage: /move <path>")
        path = Path(" ".join(args)).expanduser()
        if not path.is_absolute():
            path = (project.resolved_project / path).resolve()
        else:
            path = path.resolve()
        if not path.exists():
            return CommandResult(handled=True, output=f"Project directory does not exist: {path}")
        if not path.is_dir():
            return CommandResult(handled=True, output=f"Project path is not a directory: {path}")
        return CommandResult(
            handled=True,
            output=f"Next new Session project set to: {path}\nCurrent Session project was not changed.",
            next_new_session_project=path,
        )
    if command == "export-transcript":
        return CommandResult(handled=True, output="/export-transcript is deprecated; use /export-session", export_transcript_requested=True)
    if command == "new":
        return CommandResult(handled=True, output="Ready for new task", new_task_requested=True)
    if command == "cancel":
        return CommandResult(handled=True, output="Cancellation requested", cancel_requested=True)
    if command == "exit":
        return CommandResult(handled=True, output="Exit requested", exit_requested=True)
    return CommandResult(handled=True, output=f"Unknown command: {text.strip()}")
