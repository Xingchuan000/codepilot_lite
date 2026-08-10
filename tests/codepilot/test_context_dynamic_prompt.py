from __future__ import annotations

from pathlib import Path

from codepilot.agent.prompts import build_system_prompt
from codepilot.llm.types import ChatMessage
from codepilot.agent.boundary import RuntimeToolContext
from codepilot.multi_agent.runtime_tools import build_agent_control_registry
from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.model_context import ModelContextProfile
from codepilot.session.repositories import SessionRepositories
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


def test_native_prompt_keeps_role_instructions_without_tool_catalog() -> None:
    prompt = build_system_prompt(
        tool_specs=(_runtime_spec(),),
        agent_instructions="You are a read-only delegated explorer.",
    )

    assert "Current agent role" in prompt
    assert "read-only delegated explorer" in prompt
    assert "Do not write tool calls as JSON" in prompt
    assert "- name: read_file" not in prompt


def test_context_assembler_keeps_native_prompt_and_current_task_order(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    session = store.sessions.create_session(
        project_path=tmp_path,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
    )
    turn = store.turns.create_turn(
        session_id=session.session_id,
        title="child",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.messages.create_message(
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
    assert "Do not write tool calls as JSON" in str(messages[0].content)
    assert "delegated role" in str(messages[0].content)
    assert messages[-1].role == "user"
    assert "delegated task" in str(messages[-1].content)


def test_primary_prompt_adds_delegated_result_consumption_guidance(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "agent-control.sqlite3")
    database.initialize()
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    registry = build_agent_control_registry(
        supervisor,
        RuntimeToolContext("parent", "turn", "attempt", tmp_path),
    )

    prompt = build_system_prompt(tool_specs=registry.list_specs())

    assert "Delegated-agent result handling" in prompt
    assert "test_status=passed" in prompt
    assert "do not enter the child worktree or rerun the same tests" in prompt
    assert "Treat list_agents as authoritative runtime state" in prompt
    assert "multiple children with status=running" in prompt
    assert "do not inspect worktree timestamps" in prompt
    assert "Spawn one delegated child agent" not in prompt


def test_child_prompt_without_spawn_agent_omits_primary_agent_control_guidance() -> None:
    prompt = build_system_prompt(
        tool_specs=(_runtime_spec(),),
        agent_instructions="delegated role",
    )

    # Tool visibility is passed through the native tools request, not system-prompt text.
    read_only = ToolSpec(
        name="read_file",
        description="Read a file.",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        parameters={"path": "relative file path"},
    )
    prompt = build_system_prompt(tool_specs=(read_only,), agent_instructions="delegated role")
    assert prompt == build_system_prompt(agent_instructions="delegated role")

