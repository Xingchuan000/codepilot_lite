from __future__ import annotations

from pathlib import Path

from codepilot.agent.prompts import build_system_prompt
from codepilot.llm.types import ChatMessage
from codepilot.multi_agent.runtime_tools import AgentControlContext, build_agent_control_registry
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore
from codepilot.tools.base import DefaultPermission, ToolRisk, ToolSideEffect, ToolSpec


def _runtime_spec() -> ToolSpec:
    return ToolSpec(
        name="spawn_agent",
        description="Spawn a child agent.",
        risk=ToolRisk.LOCAL_EXECUTION,
        side_effect=ToolSideEffect.LOCAL_EXEC,
        default_permission=DefaultPermission.ALLOW,
        parameters={"task": "delegated task"},
    )


def test_explicit_tool_catalog_and_role_instructions_are_rendered() -> None:
    prompt = build_system_prompt(
        tool_specs=(_runtime_spec(),),
        agent_instructions="You are a read-only delegated explorer.",
    )

    assert "Current agent role" in prompt
    assert "read-only delegated explorer" in prompt
    assert "spawn_agent" in prompt
    assert "- name: read_file" not in prompt


def test_context_assembler_passes_dynamic_catalog_without_changing_current_task_order(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(
        project_path=tmp_path,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
    )
    turn = store.create_turn(
        session_id=session.session_id,
        title="child",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.create_message(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        role="user",
        status="completed",
        content="delegated task",
    )

    messages = ContextAssembler(database, store).build(
        session.session_id,
        turn.turn_id,
        "openai",
        "fake",
        profile=ModelContextProfile("openai", "fake", 16_384, False),
        tool_specs=(_runtime_spec(),),
        agent_instructions="delegated role",
    )

    assert isinstance(messages[0], ChatMessage)
    assert "spawn_agent" in str(messages[0].content)
    assert "delegated role" in str(messages[0].content)
    assert messages[-1].role == "user"
    assert "delegated task" in str(messages[-1].content)


def test_primary_prompt_explains_agent_control_argument_bounds(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "agent-control.sqlite3")
    database.initialize()
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    registry = build_agent_control_registry(
        supervisor,
        AgentControlContext("parent", "turn", "attempt", tmp_path),
    )

    prompt = build_system_prompt(tool_specs=registry.list_specs())

    assert "Agent control tool argument rules" in prompt
    assert "write_scope must be a JSON array" in prompt
    assert "never use an empty string or null" in prompt
    assert 'defaults to "summary_recent"' in prompt
    assert 'Never use labels such as "fork mode"' in prompt
    assert "at most 30" in prompt
    assert "never request a timeout above 30" in prompt
    assert '"tool_name":"spawn_agent"' in prompt
    assert '"write_scope":[]' in prompt


def test_child_prompt_without_spawn_agent_omits_primary_agent_control_guidance() -> None:
    prompt = build_system_prompt(
        tool_specs=(_runtime_spec(),),
        agent_instructions="delegated role",
    )

    # This fixture happens to be named spawn_agent, so verify the real guard with a read-only spec.
    read_only = ToolSpec(
        name="read_file",
        description="Read a file.",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        parameters={"path": "relative file path"},
    )
    prompt = build_system_prompt(tool_specs=(read_only,), agent_instructions="delegated role")
    assert "Agent control tool argument rules" not in prompt
