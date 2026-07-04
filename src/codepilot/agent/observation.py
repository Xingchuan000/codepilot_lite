from __future__ import annotations

from codepilot.agent.actions import AgentActionParseError
from codepilot.router.actions import ToolRouteResult

DEFAULT_OUTPUT_PREVIEW_CHARS = 1500
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
    "has_secret_like_content",
    "suggestion",
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
    important_metadata = {key: metadata[key] for key in IMPORTANT_METADATA_KEYS if key in metadata}
    if important_metadata:
        lines.append("Important metadata:")
        for key, value in important_metadata.items():
            lines.append(f"- {key}: {value}")
    output_preview = _truncate_output(route_result.result.output, max_output_chars)
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
            "Use exactly this format for tool calls:",
            '{"type":"tool_call","tool_name":"list_files","arguments":{"path":"."}}',
            'For finish, use exactly: {"type":"finish","status":"success","summary":"...","tests":"...","changed_files":[]}',
            "Return one JSON object only. Do not use Markdown.",
        ]
    )
    return "\n".join(lines)


def format_finish_blocked_observation(
    *,
    last_test_status: str | None,
    last_test_command: str | None,
) -> str:
    """把无法以 success 结束的原因清楚地反馈给模型。"""

    lines = [
        "Finish blocked.",
        "Reason: The model requested finish with status=success, but no passed run_tests result is recorded.",
        f"Current last_test_status: {last_test_status or 'unknown'}",
    ]
    if last_test_command:
        lines.append(f"Current last_test_command: {last_test_command}")
    lines.extend(
        [
            "Before finishing successfully:",
            '1. Call run_tests with a passing command, for example {"type":"tool_call","tool_name":"run_tests","arguments":{"command":"python -m pytest tests/","timeout":30}}',
            "2. Inspect git_status or git_diff if needed.",
            '3. Then return finish with status=success.',
            "Do not use finish success until run_tests has passed.",
        ]
    )
    return "\n".join(lines)
