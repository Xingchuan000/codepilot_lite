from __future__ import annotations

from codepilot.tui_agent.models import AgentRunView


def format_diff_summary(view: AgentRunView) -> str:
    lines = ["Changed files:"]
    if view.changed_files:
        lines.extend(f"- {path[:120]}" for path in view.changed_files)
    else:
        lines.append("- none")
    return "\n".join(lines)
