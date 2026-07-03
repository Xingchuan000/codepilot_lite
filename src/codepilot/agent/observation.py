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


def format_parse_error_observation(error: AgentActionParseError) -> str:
    """把动作解析错误转成清晰的修正提示。"""

    return (
        "Your previous response could not be parsed as a CodePilot AgentAction.\n"
        f"Error: {error}\n"
        'Return exactly one JSON object with type "tool_call" or "finish".'
    )
