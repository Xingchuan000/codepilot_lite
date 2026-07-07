from __future__ import annotations

import json

import pytest

from codepilot.mcp.config import MCP_CONFIG_SCHEMA_VERSION, load_mcp_config, write_example_mcp_config


def test_load_example_mcp_config() -> None:
    servers = load_mcp_config("examples/mcp/fake_filesystem_mcp.json")
    assert [server.name for server in servers] == ["filesystem"]
    assert servers[0].transport == "fake"


def test_mcp_config_validation_errors(tmp_path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"schema_version": "bad", "servers": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(config_path)

    config_path.write_text(json.dumps({"schema_version": MCP_CONFIG_SCHEMA_VERSION, "servers": [], "x": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(config_path)

    config_path.write_text(json.dumps({"schema_version": MCP_CONFIG_SCHEMA_VERSION, "servers": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(config_path)


def test_mcp_config_duplicate_and_stdio_validation(tmp_path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": MCP_CONFIG_SCHEMA_VERSION,
                "servers": [
                    {"name": "a"},
                    {"name": "a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_mcp_config(config_path)

    config_path.write_text(
        json.dumps(
            {
                "schema_version": MCP_CONFIG_SCHEMA_VERSION,
                "servers": [
                    {"name": "b", "transport": "stdio", "command": "python"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_mcp_config(config_path)


def test_write_example_mcp_config(tmp_path) -> None:
    path = write_example_mcp_config(tmp_path / "example.json")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == MCP_CONFIG_SCHEMA_VERSION
