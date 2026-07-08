from __future__ import annotations

from codepilot.tui_agent.models import AgentRunView


def format_diff_summary(view: AgentRunView) -> str:
    lines = ["Changed files:"]
    if view.changed_files:
        lines.extend(f"- {path[:120]}" for path in view.changed_files)
    else:
        lines.append("- none")
    lines.append(f"Trace: {view.trace_path or 'none'}")
    lines.append(f"Report: {view.report_path or 'none'}")
    lines.append(f"Report JSON: {view.report_json_path or 'none'}")
    return "\n".join(lines)

