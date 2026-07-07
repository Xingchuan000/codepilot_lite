from __future__ import annotations

from typing import Any

from codepilot.mcp.models import MCPCallResult, MCPServerConfig, MCPToolInfo
from codepilot.mcp.risk import classify_mcp_tool
from codepilot.mcp.trace import build_mcp_config_hash, redact_mcp_mapping, redact_mcp_text, truncate_mcp_text
from codepilot.tools.base import ToolResult


def mcp_tool_to_codepilot_spec(tool: MCPToolInfo, *, server: MCPServerConfig):
    return classify_mcp_tool(tool, server=server)


def validate_structured_content(
    structured_content: dict[str, Any],
    output_schema: dict[str, Any],
) -> tuple[bool, str | None]:
    if not output_schema:
        return True, None
    if output_schema.get("type") == "object" and not isinstance(structured_content, dict):
        return False, "structured_content must be an object"
    required = output_schema.get("required")
    if isinstance(required, list):
        missing = [str(item) for item in required if str(item) not in structured_content]
        if missing:
            return False, f"missing required keys: {', '.join(missing)}"
    return True, None


def mcp_result_to_tool_result(
    result: MCPCallResult,
    *,
    server: MCPServerConfig,
    tool: MCPToolInfo,
    codepilot_tool_name: str,
    max_output_chars: int,
) -> ToolResult:
    output, output_truncated = truncate_mcp_text(redact_mcp_text(result.content, max_chars=10**9), max_output_chars)
    error = result.error
    if error is not None:
        error, _ = truncate_mcp_text(redact_mcp_text(error, max_chars=10**9), max_output_chars)
    structured_content_present = bool(result.structured_content)
    redacted_structured_content = redact_mcp_mapping(result.structured_content)
    valid, reason = validate_structured_content(result.structured_content, tool.output_schema)
    success = result.success and valid
    config_hash = build_mcp_config_hash(server)
    metadata = {
        "mcp": True,
        "source": "mcp",
        "server_name": server.name,
        "mcp_tool_name": tool.name,
        "codepilot_tool_name": codepilot_tool_name,
        "transport": server.transport,
        "trust_level": server.trust_level,
        "descriptor_hash": tool.descriptor_hash,
        "config_hash": config_hash,
        "output_truncated": output_truncated,
        "structured_content_present": structured_content_present,
    }
    if isinstance(redacted_structured_content, dict):
        metadata["structured_content"] = redacted_structured_content
    metadata["mcp_result_metadata"] = redact_mcp_mapping(result.metadata)
    if not valid:
        metadata["warning"] = "output_schema_validation_failed"
        return ToolResult(
            success=False,
            output=output,
            error=f"MCP structured_content failed output_schema validation: {reason}",
            metadata=metadata,
        )
    return ToolResult(success=success, output=output, error=error, metadata=metadata)
