from __future__ import annotations

from codepilot.mcp.registry import MCPToolRegistry
from codepilot.trace.logger import TraceLogger
from codepilot.tools.registry import call_external_tool_traced
from codepilot.mcp.trace import redact_mcp_mapping, redact_mcp_text


def test_redact_mcp_text_masks_sensitive_values() -> None:
    text = "token=abc password=xyz Authorization: Bearer secret Cookie: a=b Set-Cookie: c=d api_key=1 private_key=2"
    redacted = redact_mcp_text(text, max_chars=4000)
    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert "Bearer secret" not in redacted
    assert "Cookie: a=b" not in redacted
    assert "api_key=1" not in redacted


def test_redact_mcp_mapping_masks_nested_sensitive_keys() -> None:
    mapping = {"nested": {"token": "abc", "ok": "value"}}
    redacted = redact_mcp_mapping(mapping)
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["ok"] == "value"


def test_mcp_trace_redacts_sensitive_text_values(tmp_path) -> None:
    registry = MCPToolRegistry.from_config("examples/mcp/fake_filesystem_mcp.json")
    logger = TraceLogger(runs_dir=tmp_path, run_id="mcp-redaction")

    call_external_tool_traced(
        "mcp.filesystem.read_file",
        external_registry=registry,
        trace_logger=logger,
        path="README.md",
        note="token=abc123 password=hunter2 Authorization: Bearer secret-token",
    )

    trace_text = logger.trace_path.read_text(encoding="utf-8")
    assert "abc123" not in trace_text
    assert "hunter2" not in trace_text
    assert "secret-token" not in trace_text
    assert "[REDACTED]" in trace_text


def test_redact_mcp_mapping_redacts_sensitive_text_values() -> None:
    value = redact_mcp_mapping({"note": "api_key=sk-test token=abc"})
    assert "sk-test" not in str(value)
    assert "abc" not in str(value)
    assert "[REDACTED]" in str(value)
