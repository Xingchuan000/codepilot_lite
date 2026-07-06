from __future__ import annotations

"""渲染第十四步 CI feedback report。"""

from pathlib import Path

from codepilot.pr_feedback.checks import summarize_check_state
from codepilot.pr_feedback.models import FeedbackItem, PRFeedbackResult


def markdown_escape_table_cell(value: object) -> str:
    """把表格单元格里的特殊字符转义掉。"""

    text = "" if value is None else str(value)
    return text.replace("|", r"\|").replace("\n", " ")


def display_path(path: Path | str | None, *, run_dir: Path) -> str:
    """把路径压缩成相对 run_dir 的可读形式。"""

    if path is None:
        return "n/a"
    resolved = Path(path)
    try:
        return str(resolved.resolve().relative_to(run_dir.resolve()))
    except Exception:
        return resolved.name


def _render_feedback_table(items: list[FeedbackItem], *, run_dir: Path) -> str:
    if not items:
        return "- none"
    lines = ["| severity | kind | title | source | file/line | evidence |", "| --- | --- | --- | --- | --- | --- |"]
    for item in items:
        file_line = item.file_path or ""
        if item.line is not None:
            file_line = f"{file_line}:{item.line}" if file_line else str(item.line)
        evidence = display_path(item.evidence_path, run_dir=run_dir) if item.evidence_path else item.raw_excerpt or ""
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape_table_cell(item.severity),
                    markdown_escape_table_cell(item.kind),
                    markdown_escape_table_cell(item.title),
                    markdown_escape_table_cell(item.source),
                    markdown_escape_table_cell(file_line or "n/a"),
                    markdown_escape_table_cell(evidence or "n/a"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def render_ci_feedback_report(*, result: PRFeedbackResult) -> str:
    """把 PRFeedbackResult 渲染成固定结构的 markdown 报告。"""

    run_dir = result.run_dir
    pr = result.pr
    freshness = result.feedback_freshness
    checks = summarize_check_state(result.checks)
    lines = [
        "# CodePilot CI Feedback Report",
        "",
        "## PR Context",
        "",
    ]
    if pr is None:
        lines.append("- unavailable")
    else:
        lines.extend(
            [
                f"- repository: {pr.owner}/{pr.repo}",
                f"- pull_number: {pr.pull_number}",
                f"- url: {pr.url}",
                f"- head_branch: {pr.head_branch}",
                f"- base_branch: {pr.base_branch}",
                f"- head_sha: {pr.head_sha or 'n/a'}",
            ]
        )
    lines.extend(["", "## Head Freshness", ""])
    if freshness is None:
        lines.append("- unavailable")
    else:
        lines.extend(
            [
                f"- observed_head_sha: {freshness.observed_head_sha or 'n/a'}",
                f"- current_head_sha: {freshness.current_head_sha or 'n/a'}",
                f"- observed_at: {freshness.observed_at or 'n/a'}",
                f"- is_stale: {'yes' if freshness.is_stale else 'no'}",
            ]
        )
        if freshness.stale_reason:
            lines.append(f"- stale_reason: {freshness.stale_reason}")
    lines.extend(["", "## Check Summary", "", "| name | source | conclusion | confidence | url |", "| --- | --- | --- | --- | --- |"])
    for check in result.checks:
        confidence = check.correlation.confidence if check.correlation else "high"
        source = check.correlation.source if check.correlation else "check_run"
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape_table_cell(check.name),
                    markdown_escape_table_cell(source),
                    markdown_escape_table_cell(check.conclusion),
                    markdown_escape_table_cell(confidence),
                    markdown_escape_table_cell(check.html_url or "n/a"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            f"- success: {checks['success']}",
            f"- failure: {checks['failure']}",
            f"- pending: {checks['pending']}",
            "",
            "## Blocking Feedback",
            "",
            _render_feedback_table([item for item in result.feedback_items if item.severity == "blocking"], run_dir=run_dir),
            "",
            "## CI Failure Summaries",
            "",
        ]
    )
    if result.log_summaries:
        for log in result.log_summaries:
            lines.extend(
                [
                    f"- {log.name}: {log.summary or 'n/a'}",
                    f"  - evidence: {display_path(log.evidence_path, run_dir=run_dir)}",
                ]
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Review Feedback",
            "",
        ]
    )
    if result.review_comments:
        for comment in result.review_comments:
            excerpt = markdown_escape_table_cell(comment.body)
            location = comment.path or "n/a"
            if comment.line is not None:
                location = f"{location}:{comment.line}"
            lines.extend([f"- {location}", f"  - {excerpt}"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Generated Artifacts",
            "",
            f"- {display_path(result.ci_status_path, run_dir=run_dir)}",
            f"- {display_path(result.review_feedback_path, run_dir=run_dir)}",
            f"- {display_path(result.ci_feedback_report_path, run_dir=run_dir)}",
            f"- {display_path(result.followup_task_path, run_dir=run_dir)}",
            f"- {display_path(result.pr_update_plan_path, run_dir=run_dir)}",
            f"- {display_path(result.ci_feedback_manifest_path, run_dir=run_dir)}",
            "",
            "## Safety Notes",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in result.blockers or ["none"])
    lines.extend(["", "## Redaction Notes", ""])
    lines.extend(
        [
            "- CI logs, review comments, and manifests are redacted before writing.",
            "- Full logs are stored only as truncated redacted summaries.",
            "- Token-like values are never written raw into report text.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_ci_feedback_report(result: PRFeedbackResult, output_path: str | Path, *, overwrite: bool = False) -> Path:
    """把 report markdown 落盘。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_ci_feedback_report(result=result), encoding="utf-8")
    return path
