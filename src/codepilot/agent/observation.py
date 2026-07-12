from __future__ import annotations

from codepilot.agent.actions import AgentActionParseError
from codepilot.router.actions import ToolRouteResult
from codepilot.tools.file_tools import LIST_FILES_PAGE_MAX_CHARS

DEFAULT_OUTPUT_PREVIEW_CHARS = 1500
LIST_FILES_OUTPUT_PREVIEW_CHARS = LIST_FILES_PAGE_MAX_CHARS
IMPORTANT_METADATA_KEYS = {
    "executed",
    "policy_decision",
    "policy_reason",
    "policy_rule",
    "requires_approval",
    "approved",
    "policy_violation",
    "status",
    "summary_line",
    "failed_tests",
    "failed_tests_truncated",
    "command",
    "returncode",
    "timed_out",
    "changed_files",
    "changed_count",
    "clean",
    "path",
    "include_hidden",
    "follow_symlinks",
    "max_depth",
    "offset",
    "max_entries",
    "entries_returned",
    "has_more",
    "next_offset",
    "limit_reason",
    "page_output_chars",
    "page_max_chars",
    "touched_paths",
    "changed",
    "dry_run",
    "include_content",
    "staged",
    "truncated",
    "preview_truncated",
    "output_truncated",
    "line_truncated",
    "char_truncated",
    "observation_output_truncated",
    "has_secret_like_content",
    "suggestion",
    "mcp",
    "source",
    "server_name",
    "mcp_tool_name",
    "codepilot_tool_name",
    "transport",
    "trust_level",
    "descriptor_hash",
    "config_hash",
    "exposed_to_agent",
    "exposure_reason",
    "risk_source",
    "output_schema_validation_failed",
    "structured_content_present",
}


def _truncate_output(text: str, max_output_chars: int) -> str:
    """限制 observation 中的输出预览长度。"""

    if len(text) <= max_output_chars:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, max_output_chars - len(suffix))]}{suffix}"


def format_observation(route_result: ToolRouteResult, *, max_output_chars: int = DEFAULT_OUTPUT_PREVIEW_CHARS) -> str:
    """把 ToolRouteResult 渲染为下一轮 user message。"""

    metadata = route_result.result.metadata
    executed = metadata.get("executed")
    if executed is None:
        executed = route_result.success or metadata.get("policy_decision") not in {"deny", "ask"}
    lines = [
        f"Tool: {route_result.tool_name}",
        f"Executed: {'true' if executed else 'false'}",
        f"Success: {'true' if route_result.success else 'false'}",
    ]
    if metadata.get("policy_decision") is not None:
        lines.append(f"Policy: {metadata['policy_decision']}")
    if route_result.result.output_summary:
        lines.append(f"Summary: {route_result.result.output_summary}")
    elif route_result.result.error:
        lines.append(f"Summary: {route_result.result.error}")
    if route_result.result.error:
        lines.append(f"Error: {route_result.result.error}")
    output_limit = LIST_FILES_OUTPUT_PREVIEW_CHARS if route_result.tool_name == "list_files" else max_output_chars
    output_preview = _truncate_output(route_result.result.output, output_limit)
    render_metadata = dict(metadata)
    if route_result.tool_name == "list_files" and output_preview != route_result.result.output:
        render_metadata["observation_output_truncated"] = True
    important_metadata = {key: render_metadata[key] for key in IMPORTANT_METADATA_KEYS if key in render_metadata}
    if important_metadata:
        lines.append("Important metadata:")
        for key, value in important_metadata.items():
            lines.append(f"- {key}: {value}")
    if output_preview:
        lines.append("Output preview:")
        lines.append(output_preview)
    return "\n".join(lines)


def format_parse_error_observation(error: Exception) -> str:
    """把动作解析错误转成更具体的修正提示。"""

    norm_meta = getattr(error, "normalization_metadata", {}) or {}
    non_standard_fields = norm_meta.get("non_standard_fields", [])
    normalized_fields = norm_meta.get("normalized_fields", {})
    lines = [
        "Action parse failed.",
        f"Reason: {error}",
    ]
    if non_standard_fields:
        lines.append("Your previous action used non-standard fields: " + ", ".join(str(item) for item in non_standard_fields) + ".")
    if normalized_fields:
        lines.append(f"I attempted to normalize these fields: {normalized_fields}.")
    lines.extend(
        [
            "普通回复可以直接用自然文本。",
            "只有调用工具或提交结构化 finish 时，才需要输出单个 JSON 对象。",
            "Use exactly this format for tool calls:",
            '{"type":"tool_call","tool_name":"list_files","arguments":{"path":"."}}',
            'For finish, use exactly: {"type":"finish","status":"success","summary":"...","tests":"...","changed_files":[]}',
            "Return one JSON object only. Do not use Markdown.",
        ]
    )
    return "\n".join(lines)


def format_finish_blocked_observation(
    *,
    missing_evidence: list[str],
    last_test_status: str | None,
    last_test_command: str | None,
    diff_checked: bool,
    written_files: list[str],
) -> str:
    """把无法以 success 结束的原因清楚地反馈给模型。"""

    lines = ["Finish blocked.", f"Missing evidence: {', '.join(missing_evidence) if missing_evidence else 'unknown'}."]
    if "missing_changed_files" in missing_evidence:
        lines.append("This task needs real code changes, so please use read/edit tools first. If no code change is needed, finish with partial instead of success.")
    if "missing_passed_tests" in missing_evidence:
        lines.append("Please run the relevant tests until they pass.")
    if "missing_diff_check" in missing_evidence:
        lines.append("Please call git_diff after edits so the loop can verify the diff evidence.")
    lines.append(f"Current last_test_status: {last_test_status or 'unknown'}")
    if last_test_command:
        lines.append(f"Current last_test_command: {last_test_command}")
    lines.append(f"Current diff_checked: {'true' if diff_checked else 'false'}")
    lines.append(f"Current written_files: {', '.join(written_files) if written_files else 'none'}")
    lines.extend(
        [
            "Before finishing successfully:",
            '1. Make sure the real file changes have been executed, not just declared in finish.changed_files.',
            '2. Call run_tests with a passing command, for example {"type":"tool_call","tool_name":"run_tests","arguments":{"command":"python -m pytest tests/","timeout":30}}',
            "3. Call git_diff to confirm the final diff.",
            '4. Then return finish with status=success.',
        ]
    )
    return "\n".join(lines)
