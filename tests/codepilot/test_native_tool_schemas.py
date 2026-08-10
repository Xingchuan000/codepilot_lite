from __future__ import annotations

import pytest

from codepilot.mcp.models import MCPServerConfig, MCPToolInfo
from codepilot.mcp.risk import classify_mcp_tool
from codepilot.multi_agent.runtime_tools import AgentControlContext, _tool_specs, build_agent_control_registry
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.session.database import SessionDatabase
from codepilot.tools.registry import list_tool_specs


def test_core_and_runtime_specs_expose_object_schemas_without_repo() -> None:
    specs = [*list_tool_specs(), *_tool_specs()]

    assert specs
    for spec in specs:
        assert spec.input_schema["type"] == "object"
        assert "repo" not in spec.input_schema.get("properties", {})
        assert spec.input_schema.get("additionalProperties") is False


def test_mcp_spec_keeps_input_schema_verbatim() -> None:
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    spec = classify_mcp_tool(
        MCPToolInfo(server_name="filesystem", name="read_file", input_schema=schema),
        server=MCPServerConfig(name="filesystem"),
    )

    assert spec.input_schema == schema


def test_mcp_spec_rejects_non_object_input_schema() -> None:
    with pytest.raises(ValueError, match="must have type 'object'"):
        classify_mcp_tool(
            MCPToolInfo(server_name="filesystem", name="bad", input_schema={"type": "array"}),
            server=MCPServerConfig(name="filesystem"),
        )


def test_m2_agent_control_schemas_keep_exact_types_and_bounds(tmp_path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    registry = build_agent_control_registry(
        AgentSupervisor(database=database, child_runtime_factory=lambda _: None),
        AgentControlContext("parent", "turn", "attempt", tmp_path),
    )

    spawn = registry.find_spec("spawn_agent")
    wait = registry.find_spec("wait_agent")
    assert spawn is not None
    assert wait is not None
    assert spawn.input_schema["properties"]["write_scope"]["type"] == "array"
    assert spawn.input_schema["properties"]["write_scope"]["items"]["type"] == "string"
    assert set(spawn.input_schema["properties"]["context_mode"]["enum"]) == {
        "none",
        "recent",
        "summary",
        "summary_recent",
        "full",
    }
    timeout = wait.input_schema["properties"]["timeout_seconds"]
    assert max(option.get("maximum", 0) for option in timeout["anyOf"]) == 30


def test_m2_invalid_arguments_are_rejected_without_alias_or_repair(tmp_path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    registry = build_agent_control_registry(
        AgentSupervisor(database=database, child_runtime_factory=lambda _: None),
        AgentControlContext("parent", "turn", "attempt", tmp_path),
    )

    invalid_scope = registry.call("spawn_agent", {"agent_type": "general", "task": "inspect", "write_scope": "src/"})
    invalid_mode = registry.call("spawn_agent", {"agent_type": "general", "task": "inspect", "context_mode": "fork mode"})
    invalid_timeout = registry.call("wait_agent", {"agent_id": "child", "timeout_seconds": 120})

    assert invalid_scope.success is False
    assert "list" in (invalid_scope.error or "").lower()
    assert invalid_mode.success is False
    assert "literal" in (invalid_mode.error or "").lower() or "summary_recent" in (invalid_mode.error or "")
    assert invalid_timeout.success is False
    assert "30" in (invalid_timeout.error or "")
