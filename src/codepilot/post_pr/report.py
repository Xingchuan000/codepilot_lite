from __future__ import annotations

"""第十五步 Post-PR automation 的 Markdown 报告。"""

from pathlib import Path
from codepilot.pr_feedback.report import markdown_escape_table_cell
from codepilot.post_pr.models import PostPRAutomationResult, PostPRAutomationState, SideEffectLedger


def _display_path(path: Path | str | None, *, run_dir: Path) -> str:
    if path is None:
        return "n/a"
    resolved = Path(path)
    try:
        return str(resolved.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        return resolved.name


def _manual_next_command(result: PostPRAutomationResult) -> str:
    run_dir = str(result.run_dir)
    if result.terminal_reason == "awaiting_approval":
        return f"codepilot post-pr --run-dir {run_dir} --execute --approval-file post_pr/approval_decision.json --resume"
    if result.terminal_reason == "patch_ready":
        return f"codepilot post-pr --run-dir {run_dir} --execute --approve-push-update --resume"
    if result.terminal_reason == "repeated_feedback":
        return "请人工查看 repeated fingerprints 和 ci_feedback_report.md。"
    if result.terminal_reason == "pending_checks":
        return f"codepilot post-pr --run-dir {run_dir} --wait-ci --resume"
    if result.terminal_reason == "no_feedback":
        return "无需继续自动修复。"
    return "请根据报告中的安全门和审批状态手动处理。"


def render_post_pr_automation_report(
    result: PostPRAutomationResult,
    *,
    state: PostPRAutomationState | None = None,
    side_effects: SideEffectLedger | None = None,
) -> str:
    lines = [
        "# CodePilot Post-PR Automation Report",
        "",
        "## Run / PR Context",
        "",
        f"- Run ID: {result.run_id}",
        f"- Run Dir: {result.run_dir}",
        f"- Post-PR Dir: {result.post_pr_dir}",
        "",
        "## Automation Mode",
        "",
        f"- Status: {result.status}",
        f"- Terminal Reason: {result.terminal_reason}",
        f"- Rounds: {len(result.rounds)}",
        "",
        "## Round Timeline",
        "",
        "| round | phase | status | terminal reason | fingerprints |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in result.rounds:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape_table_cell(item.round_id),
                    markdown_escape_table_cell("execute" if item.execute_manifest_path else "collect"),
                    markdown_escape_table_cell(item.status),
                    markdown_escape_table_cell(item.terminal_reason),
                    markdown_escape_table_cell(", ".join(item.feedback_fingerprints) if item.feedback_fingerprints else "none"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Feedback Delta",
            "",
        ]
    )
    for item in result.rounds:
        lines.append(f"- {item.round_id}: {', '.join(item.feedback_fingerprints) if item.feedback_fingerprints else 'none'}")
    lines.extend(
        [
            "",
            "## Approval Scope",
            "",
            f"- Approval request: {_display_path(result.approval_request_path, run_dir=result.run_dir)}",
            f"- Approval decision: {_display_path(result.approval_decision_path, run_dir=result.run_dir)}",
            "",
            "## Side Effects Ledger Summary",
            "",
            f"- Side effects: {_display_path(result.side_effects_path, run_dir=result.run_dir)}",
        ]
    )
    if side_effects:
        for effect in side_effects.effects:
            lines.append(f"- {effect.round_id}: {effect.action} / {effect.status} / {effect.commit_sha or 'n/a'}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Manifest: {_display_path(result.manifest_path, run_dir=result.run_dir)}",
            f"- Report: {_display_path(result.report_path, run_dir=result.run_dir)}",
            f"- Workflow: {_display_path(result.workflow_path, run_dir=result.run_dir)}",
            "",
            "## Safety Notes",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in (state.blockers if state else ()) or result.blockers or ["none"])
    lines.extend(
        [
            "",
            "## Manual Next Command",
            "",
            _manual_next_command(result),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_post_pr_automation_report(
    result: PostPRAutomationResult,
    output_path: str | Path,
    *,
    state: PostPRAutomationState | None = None,
    side_effects: SideEffectLedger | None = None,
    overwrite: bool = False,
) -> Path:
    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_post_pr_automation_report(result, state=state, side_effects=side_effects), encoding="utf-8")
    return path
