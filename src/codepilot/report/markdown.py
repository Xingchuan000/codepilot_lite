from __future__ import annotations

import re

from codepilot.report.models import PolicyViolationReport, RunReport, ToolStepReport

_MAX_DIFF_PREVIEW_CHARS = 4000


def _markdown_escape_table_cell(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("|", r"\|").replace("\n", "<br>")


def _format_bool(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _format_policy(step: ToolStepReport) -> str:
    if step.policy_decision is None:
        return "unknown"
    if step.policy_decision == "ask":
        if step.approved is True:
            return "ask/approved"
        if step.approved is False:
            return "ask/not approved"
    return step.policy_decision


def _code_path(path: str | None) -> str:
    return f"`{path}`" if path is not None else "unknown"


def _bullet_list(items: list[str], empty: str = "None.") -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def _truncate_text(text: str, max_chars: int = _MAX_DIFF_PREVIEW_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}", True


def _sanitize_note(text: str) -> str:
    """把错误和 warning 再做一次轻量脱敏，避免直接展示敏感赋值。"""

    patterns = [
        re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token)\s*=\s*([^\s]+)"),
        re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token)\s*:\s*([^\s]+)"),
    ]
    sanitized = text
    for pattern in patterns:
        sanitized = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", sanitized)
    return sanitized


def _policy_violation_row(violation: PolicyViolationReport) -> str:
    return (
        "| "
        f"{violation.step if violation.step is not None else ''} | "
        f"{_markdown_escape_table_cell(violation.tool_name)} | "
        f"{_markdown_escape_table_cell(violation.decision)} | "
        f"{_format_bool(violation.approved)} | "
        f"{_format_bool(violation.executed)} | "
        f"{_markdown_escape_table_cell(violation.reason)} |"
    )


def render_markdown_report(report: RunReport) -> str:
    """把 RunReport 渲染成固定结构的 Markdown。"""

    lines: list[str] = ["# CodePilot Lite Run Report", ""]

    lines.extend(
        [
            "## 1. Run Summary",
            f"- Run ID: {_markdown_escape_table_cell(report.run_id)}",
            f"- Status: {_markdown_escape_table_cell(report.status or 'unknown')}",
            f"- Success: {_format_bool(report.success)}",
            f"- Repository: {_code_path(report.repo)}",
            f"- Model: {_markdown_escape_table_cell(report.model or 'unknown')}",
            f"- Policy Mode: {_markdown_escape_table_cell(report.policy_mode or 'unknown')}",
            f"- Steps: {report.steps}",
            f"- Trace: {_code_path(report.trace_path)}",
            "",
            "## 2. Task",
            report.task or "None.",
            "",
            "## 3. Final Result",
            report.final_summary or "None.",
            "",
            "## 4. Tool Timeline",
            "| Step | Tool | Policy | Executed | Success | Summary |",
            "|---:|---|---|---:|---:|---|",
        ]
    )
    for step in report.tool_steps:
        lines.append(
            "| "
            f"{step.step if step.step is not None else ''} | "
            f"{_markdown_escape_table_cell(step.tool_name)} | "
            f"{_markdown_escape_table_cell(_format_policy(step))} | "
            f"{_format_bool(step.executed)} | "
            f"{_format_bool(step.success)} | "
            f"{_markdown_escape_table_cell(step.summary or step.error or '')} |"
        )

    lines.extend(
        [
            "",
            "## 5. Files Changed",
            _bullet_list([_code_path(path) for path in report.changed_files]),
            "",
            "## 6. Test Result",
            f"- Status: {_markdown_escape_table_cell(report.tests.status or 'unknown')}",
            f"- Command: {_markdown_escape_table_cell(report.tests.command or 'unknown')}",
            f"- Original command: {_markdown_escape_table_cell(report.tests.original_command or 'unknown')}",
            f"- Executed command: {_markdown_escape_table_cell(report.tests.executed_command or 'unknown')}",
            f"- Return code: {report.tests.returncode if report.tests.returncode is not None else 'unknown'}",
            f"- Timed out: {_format_bool(report.tests.timed_out)}",
            f"- Failed tests: {_markdown_escape_table_cell(', '.join(report.tests.failed_tests) if report.tests.failed_tests else 'none')}",
            f"- Summary: {_markdown_escape_table_cell(report.tests.summary or 'None.')}",
            "",
            "## 7. Diff Summary",
        ]
    )
    if not report.diff.checked:
        lines.append("Diff was not checked.")
    else:
        lines.extend(
            [
                f"- Checked: yes",
                f"- Paths: {_markdown_escape_table_cell(', '.join(report.diff.paths) if report.diff.paths else 'None.')}",
                f"- Summary: {_markdown_escape_table_cell(report.diff.summary or 'None.')}",
                f"- Truncated: {_format_bool(report.diff.truncated)}",
            ]
        )
        if report.diff.preview:
            preview, _ = _truncate_text(report.diff.preview, _MAX_DIFF_PREVIEW_CHARS)
            lines.extend(["", "```diff", preview, "```"])
        else:
            lines.append("None.")

    lines.extend(
        [
            "",
            "## 8. Policy Summary",
            f"- Total: {report.policy.total}",
            f"- Allowed: {report.policy.allowed}",
            f"- Asked: {report.policy.asked}",
            f"- Approved: {report.policy.approved}",
            f"- Denied: {report.policy.denied}",
        ]
    )
    if report.policy.violations:
        lines.extend(
            [
                "",
                "| Step | Tool | Decision | Approved | Executed | Reason |",
                "|---:|---|---|---:|---:|---|",
            ]
        )
        for violation in report.policy.violations:
            lines.append(_policy_violation_row(violation))
    else:
        lines.extend(["", "Policy violations: none."])

    lines.extend(
        [
            "",
            "## 9. Failure / Warning Notes",
        ]
    )
    notes = [f"ERROR: {_sanitize_note(item)}" for item in report.errors] + [f"WARNING: {_sanitize_note(item)}" for item in report.warnings]
    lines.append(_bullet_list(notes, empty="None."))
    return "\n".join(lines).rstrip() + "\n"
