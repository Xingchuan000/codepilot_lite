from __future__ import annotations

from codepilot.session.models import BranchConfirmationRequired
from codepilot.session.reconcilers import ReconciliationResult


def format_branch_confirmation(request: BranchConfirmationRequired) -> str:
    """生成分支变化确认弹窗正文。"""

    return f"Session branch: {request.old_branch or '(none)'}\nCurrent branch: {request.new_branch or '(none)'}\nContinue and update this Session to new branch?"


def format_recovery_modal(tool_name: str, arguments: dict, started_at: str | None, result: ReconciliationResult | None) -> str:
    """生成恢复弹窗正文，明确展示未知副作用风险。"""

    lines = [f"Tool: {tool_name}", f"Arguments: {arguments}", f"execution_started: {started_at or '(unknown)'}"]
    if result is not None:
        lines.extend([f"Reconciliation: {result.decision.value}", result.detail])
    lines.append("Actions: inspect / mark completed / retry / abort")
    return "\n".join(lines)
