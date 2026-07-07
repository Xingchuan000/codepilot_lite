from __future__ import annotations

import pytest

from codepilot.mcp.models import MCPCallResult, MCPServerConfig, MCPToolInfo


def test_mcp_models_defaults_and_validation() -> None:
    server = MCPServerConfig(name="filesystem")
    assert server.transport == "fake"
    assert server.trust_level == "fake"
    assert MCPToolInfo(server_name="filesystem", name="read_file").side_effect_hint == "unknown"
    assert "success" in MCPCallResult(success=True).model_dump_json()


def test_trusted_annotations_require_trusted_server() -> None:
    with pytest.raises(ValueError):
        MCPServerConfig(name="filesystem", trust_level="local_untrusted", trusted_annotations=True)
