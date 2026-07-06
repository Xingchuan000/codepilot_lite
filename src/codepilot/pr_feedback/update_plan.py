from __future__ import annotations

"""渲染第十四步 PR update plan。"""

from pathlib import Path

from codepilot.pr_feedback.models import PRFeedbackResult


def render_pr_update_plan(
    *,
    result: PRFeedbackResult,
    dry_run: bool,
    execute: bool,
    allow_run_agent: bool,
    allow_push_update: bool,
    allow_comment: bool,
) -> str:
    """把当前 feedback 状态压缩成可读的更新计划。"""

    blocking_count = len([item for item in result.feedback_items if item.severity == "blocking"])
    pending_count = len([check for check in result.checks if check.conclusion == "pending"])
    stale = result.feedback_freshness.is_stale if result.feedback_freshness else False
    lines = [
        "# CodePilot PR Update Plan",
        "",
        "## Current PR State",
        "",
        f"- PR: {result.pr.url if result.pr else 'n/a'}",
        f"- Status: {result.status}",
        f"- Mode: {'execute' if execute else 'dry-run'}",
        f"- Dry run: {'yes' if dry_run else 'no'}",
        f"- Blocking feedback: {blocking_count}",
        f"- Pending checks: {pending_count}",
        f"- Head stale: {'yes' if stale else 'no'}",
        f"- Follow-up task: {result.followup_task_path or 'n/a'}",
        "",
        "## Execution Gates",
        "",
        f"- allow_run_agent: {'yes' if allow_run_agent else 'no'}",
        f"- allow_push_update: {'yes' if allow_push_update else 'no'}",
        f"- allow_comment: {'yes' if allow_comment else 'no'}",
        "",
        "## Explicit Non-Goals",
        "",
        "- This workflow will not merge the PR.",
        "- This workflow will not approve review requests.",
        "- This workflow will not force push.",
        "- This workflow will not push the base branch.",
        "- This workflow will not resolve review threads.",
        "",
        "## Manual Commands",
        "",
        "```bash",
        "PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run_id> --dry-run --overwrite",
        "PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run_id> --execute --allow-run-agent --overwrite",
        "PYTHONPATH=src python -m codepilot.cli pr-feedback --run-dir runs/<run_id> --execute --allow-run-agent --allow-push-update --overwrite",
        "```",
    ]
    if result.blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- {item}" for item in result.blockers)
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in result.warnings)
    return "\n".join(lines).rstrip() + "\n"


def write_pr_update_plan(content: str, output_path: str | Path, *, overwrite: bool = False) -> Path:
    """把 update plan 写到磁盘。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
