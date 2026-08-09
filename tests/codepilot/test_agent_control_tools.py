from __future__ import annotations

from pathlib import Path

from codepilot.multi_agent.runtime_tools import AgentControlContext, build_agent_control_registry
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.session.database import SessionDatabase


def test_control_registry_exposes_only_runtime_control_tools(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    registry = build_agent_control_registry(
        supervisor,
        AgentControlContext("parent", "turn", "attempt", tmp_path),
    )

    assert {spec.name for spec in registry.list_specs()} == {
        "spawn_agent",
        "wait_agent",
        "list_agents",
        "close_agent",
        "inspect_agent_patch",
        "apply_agent_patch",
    }
    assert registry.find_spec("spawn_agent").parameters["agent_type"]
    assert registry.find_spec("apply_agent_patch").default_permission.value == "ask"


def test_control_registry_validates_arguments_as_structured_tool_result(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    registry = build_agent_control_registry(
        supervisor,
        AgentControlContext("parent", "turn", "attempt", tmp_path),
    )

    result = registry.call("spawn_agent", {"agent_type": "not-a-role", "task": "x"})

    assert result.success is False
    assert "validation" in (result.error or "").lower() or "literal" in (result.error or "").lower()


def test_control_registry_documents_exact_argument_contracts(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    registry = build_agent_control_registry(
        supervisor,
        AgentControlContext("parent", "turn", "attempt", tmp_path),
    )

    spawn = registry.find_spec("spawn_agent")
    wait = registry.find_spec("wait_agent")
    assert spawn is not None
    assert wait is not None

    assert "JSON array[string]" in str(spawn.parameters["write_scope"])
    assert "never pass a string or null" in str(spawn.parameters["write_scope"])
    assert '"summary_recent"' in str(spawn.parameters["context_mode"])
    assert '"fork mode"' in str(spawn.parameters["context_mode"])
    assert "0..10" in str(spawn.parameters["recent_turns"])
    assert "<= 30" in str(wait.parameters["timeout_seconds"])
    assert "at most 30 seconds" in wait.description
