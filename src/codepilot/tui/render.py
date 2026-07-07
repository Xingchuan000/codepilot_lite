from __future__ import annotations

from collections import Counter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from codepilot.tui.models import RunDashboardModel, RunIndexEntry
from codepilot.tui.redaction import truncate_text


def _short(value: object | None, *, max_chars: int = 80) -> str:
    if value is None:
        return ""
    return truncate_text(str(value), max_chars=max_chars)


def render_run_index(console: Console, entries: list[RunIndexEntry]) -> None:
    if not entries:
        console.print("No runs found")
        return
    table = Table(title="Run Dashboard")
    for column in ["Run ID", "Type", "Status", "Updated", "Tools", "Policy Deny", "Tests", "Changed", "Artifacts", "MCP", "Task"]:
        table.add_column(column)
    for entry in entries:
        artifact_kinds = Counter(item.kind for item in entry.artifacts)
        core_kinds = ", ".join(sorted(kind for kind in artifact_kinds if kind in {"report_json", "patch", "artifact_manifest", "pr_summary", "issue_json"}))
        table.add_row(
            entry.run_id,
            entry.run_type,
            entry.status,
            _short(entry.updated_at, max_chars=24),
            str(entry.tool_call_count),
            str(entry.policy_denied_count),
            entry.test_status or "",
            str(len(entry.changed_files)),
            f"{len(entry.artifacts)} {core_kinds}".strip(),
            "yes" if entry.has_mcp else "no",
            _short(entry.task, max_chars=80),
        )
    console.print(table)


def render_run_detail(console: Console, model: RunDashboardModel) -> None:
    entry = model.entry
    console.print(
        Panel(
            Text(
                "\n".join(
                    [
                        f"Run: {entry.run_id}",
                        f"Type: {entry.run_type}",
                        f"Status: {entry.status}",
                        f"Task: {_short(entry.task, max_chars=120)}",
                        f"Started: {entry.started_at or ''}",
                        f"Ended: {entry.ended_at or ''}",
                    ]
                )
            ),
            title="Header",
        )
    )
    summary = Table(title="Summary")
    summary.add_column("Metric")
    summary.add_column("Value")
    for metric, value in [
        ("Tool total", model.entry.tool_call_count),
        ("Policy allow", model.policy_summary.get("allow", 0)),
        ("Policy ask", model.policy_summary.get("ask", 0)),
        ("Policy deny", model.policy_summary.get("deny", 0)),
        ("Test status", model.test_summary.get("status")),
        ("Changed files", len(model.entry.changed_files)),
        ("MCP total", model.mcp_summary.get("total_tool_calls", 0)),
        ("MCP hashes", ", ".join(model.mcp_summary.get("descriptor_hashes", [])) if isinstance(model.mcp_summary.get("descriptor_hashes"), list) else ""),
        ("Artifact count", len(model.artifact_summary)),
    ]:
        summary.add_row(metric, _short(value))
    console.print(summary)

    timeline = Table(title="Timeline")
    for column in ["Step", "Category", "Event", "Tool", "Status", "Policy", "Executed", "Summary"]:
        timeline.add_column(column)
    for row in model.timeline:
        timeline.add_row(
            _short(row.step),
            row.category,
            row.title,
            _short(row.tool_name),
            _short(row.status),
            _short(row.policy_decision),
            _short(row.executed),
            _short(row.output_summary),
        )
    console.print(timeline)

    changed = Table(title="Changed Files")
    changed.add_column("Path")
    for path in model.entry.changed_files[:50]:
        changed.add_row(_short(path, max_chars=120))
    if len(model.entry.changed_files) > 50:
        changed.add_row("... truncated")
    console.print(changed)

    artifacts = Table(title="Artifacts")
    for column in ["Kind", "Path", "Exists", "Size", "Verified", "Sha256", "Warnings"]:
        artifacts.add_column(column)
    for item in model.artifact_summary:
        artifacts.add_row(
            item.kind,
            _short(item.path, max_chars=120),
            str(item.exists).lower(),
            str(item.size_bytes),
            "" if item.verified is None else str(item.verified).lower(),
            _short((item.sha256 or "")[:12]),
            ", ".join(item.warnings),
        )
    console.print(artifacts)

    if model.warnings:
        console.print(Panel("\n".join(model.warnings), title="Warnings"))
