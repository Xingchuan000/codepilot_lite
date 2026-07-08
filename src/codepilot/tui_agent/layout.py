from __future__ import annotations

from codepilot.tui_agent.models import AgentRunView, PermissionMode, ProjectContext, TUISession
from codepilot.tui_agent.status import model_label
from codepilot.tui_agent.diff_view import format_diff_summary


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
            f"Changed files: {', '.join(view.changed_files) if view.changed_files else 'none'}",
            f"Tests: {view.test_status or 'unknown'}",
            f"Report: {view.report_path or 'none'}",
            f"Report JSON: {view.report_json_path or 'none'}",
            f"Trace: {view.trace_path or 'none'}",
            "Next: /help /status /permissions /diff /report /new /cancel /exit",
            format_diff_summary(view),
        ]
    )

