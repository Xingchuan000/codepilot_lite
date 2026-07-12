from __future__ import annotations

import json
import re

from codepilot.tui_agent.models import AgentRunView, PermissionMode, ProjectContext, TUISession
from codepilot.tui_agent.models import TranscriptItem
from codepilot.tui_agent.status import model_label
from codepilot.tui_agent.diff_view import format_diff_summary


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def format_header(project: ProjectContext, session: TUISession, view: AgentRunView, permission_mode: PermissionMode) -> str:
    return "\n".join(
        [
            f"Project: {project.resolved_project}",
            f"Git: {project.git_root or 'non-git'} ({project.git_dirty_status})",
            f"Model: {model_label(session.model)}",
            f"Permission: {permission_mode}",
            f"Run Status: {view.status}",
        ]
    )


def format_main_log(view: AgentRunView) -> str:
    lines = [f"Task: {view.task or 'idle'}", f"Run: {view.run_id or 'none'}"]
    if view.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in view.warnings)
    return "\n".join(lines)


def _preview_text(value: dict[str, object] | None) -> str:
    if not value:
        return "{}"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def format_transcript_item(item: TranscriptItem) -> str:
    if item.kind == "user_message":
        return f"You: {item.body}"
    if item.kind == "assistant_plan":
        return f"+ Plan: {item.body}"
    if item.kind == "assistant_raw":
        return f"Assistant: {item.body}"
    if item.kind == "assistant_action":
        preview = _preview_text(item.input_preview)
        return f"→ {item.tool_name or ''} {preview}".rstrip()
    if item.kind == "tool_result":
        return "\n".join(filter(None, [f"{'✓' if item.status == 'success' else '✗'} {item.tool_name or 'tool'}", item.body]))
    if item.kind == "permission_request":
        return "\n".join(filter(None, [f"? Permission required: {item.tool_name or 'tool'}", item.body]))
    if item.kind == "permission_response":
        return "✓ Approved once" if item.status == "approved" else "✗ Denied"
    if item.kind == "final_summary":
        return f"Final: {item.body}"
    if item.kind == "command_output":
        return "\n".join(filter(None, [item.title or "$ command", item.body]))
    if item.kind == "system_status":
        return f"• {item.body}"
    if item.kind == "error":
        return f"! {item.body}"
    return item.body or item.title


def transcript_item_to_copy_text(item: TranscriptItem) -> str:
    if item.copy_text:
        return _strip_ansi(item.copy_text)
    return _strip_ansi(format_transcript_item(item))


def format_transcript_plain(items: tuple[TranscriptItem, ...]) -> str:
    return "\n\n".join(transcript_item_to_copy_text(item) for item in items)


def _short_project_path(project: ProjectContext) -> str:
    name = project.resolved_project.name
    return name if name else str(project.resolved_project)


def _format_requirement(value: bool | None) -> str:
    if value is True:
        return "required"
    if value is False:
        return "not required"
    return "unknown"


def _format_test_state(view: AgentRunView) -> str:
    if view.test_status:
        return view.test_status
    if view.tests_required is True:
        return "required (missing)"
    if view.tests_required is False:
        return "not required"
    return "unknown"


def _format_diff_state(view: AgentRunView) -> str:
    if view.diff_checked is True:
        return "checked"
    if view.diff_required is True:
        return "required (missing)"
    if view.diff_required is False:
        return "not required"
    return "unknown"


def format_side_status(project: ProjectContext, session: TUISession, view: AgentRunView, permission_mode: PermissionMode) -> str:
    return "\n".join(
        [
            f"Project: {_short_project_path(project)}",
            f"Git: {(project.git_root.name if project.git_root else 'non-git')} ({project.git_dirty_status})",
            f"Model: {model_label(session.model)}",
            f"Permission: {permission_mode}",
            f"Status: {view.status}",
            f"Completion: {view.completion_kind or 'unknown'}",
            f"Tool: {view.active_tool or view.current_tool or 'none'}",
            f"Changed: {len(view.changed_files)}",
            f"Evidence: {_format_requirement(view.requires_evidence)}",
            f"Tests: {_format_test_state(view)}",
            f"Diff: {_format_diff_state(view)}",
            "Commands: /help /status /permissions /diff /report /trace /copy /move /export-transcript /cancel /exit",
        ]
    )


def format_timeline(view: AgentRunView) -> str:
    if not view.timeline:
        return "Timeline: empty"
    return "\n".join(
        f"{item.step or '-'} | {item.tool_name or item.category} | {item.status or ''} | {item.policy_decision or ''} | {item.output_summary or item.title}"
        for item in view.timeline
    )


def format_result_panel(view: AgentRunView) -> str:
    return "\n".join(
        [
            f"Status: {view.status}",
            f"Completion: {view.completion_kind or 'unknown'}",
            f"Assistant stop: {view.assistant_stop_reason or 'unknown'}",
            f"Evidence: {_format_requirement(view.requires_evidence)}",
            f"Write attempted: {_format_requirement(view.write_attempted)}",
            f"Write executed: {_format_requirement(view.write_executed)}",
            f"Tests: {_format_test_state(view)}",
            f"Diff: {_format_diff_state(view)}",
            f"Missing evidence: {', '.join(view.missing_evidence) if view.missing_evidence else 'none'}",
            f"Changed files: {', '.join(view.changed_files) if view.changed_files else 'none'}",
            f"Test status: {view.test_status or 'unknown'}",
            f"Report: {view.report_path or 'none'}",
            f"Report JSON: {view.report_json_path or 'none'}",
            f"Trace: {view.trace_path or 'none'}",
            "Next: /help /status /permissions /diff /report /new /cancel /exit",
            format_diff_summary(view),
        ]
    )
