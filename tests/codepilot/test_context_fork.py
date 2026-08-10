from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.session.context import ContextAssembler
from codepilot.session.context_fork import ForkContextPolicy
from codepilot.session.database import SessionDatabase
from codepilot.session.model_context import ModelContextProfile
from codepilot.session.service import SessionService
from codepilot.session.repositories import SessionRepositories


def _fixture(tmp_path: Path, mode: str, recent_turns: int = 3):
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    service = SessionService(database)
    parent = service.create_session(tmp_path / "repo", "openai", "fake", "manual")

    turns = []
    for index in range(3):
        turn = store.turns.create_turn(
            session_id=parent.session_id,
            title=f"parent {index + 1}",
            provider_snapshot="openai",
            model_snapshot="fake",
            permission_mode_snapshot="manual",
            branch_snapshot=None,
        )
        turns.append(turn)
        store.messages.create_message(
            session_id=parent.session_id,
            turn_id=turn.turn_id,
            role="user",
            status="completed",
            content=f"parent-task-{index + 1}",
        )
        assistant = store.messages.create_message(
            session_id=parent.session_id,
            turn_id=turn.turn_id,
            role="assistant",
            status="completed",
            content=f"parent-result-{index + 1}",
        )
        store.messages.append_message_part(assistant.message_id, type="text", content=f"parent-result-{index + 1}")
    store.context_summaries.create_context_summary(
        session_id=parent.session_id,
        turn_id=turns[0].turn_id,
        content="summary-before-fork",
        source_end_sequence=turns[0].sequence,
        model="fake",
    )
    child = service.create_child_session(
        parent_session_id=parent.session_id,
        forked_from_turn_id=turns[1].turn_id,
        provider="openai",
        model="fake",
        permission_mode="manual",
        metadata={"context_fork": {"mode": mode, "recent_turns": recent_turns}},
    )
    child_turn = store.turns.create_turn(
        session_id=child.session_id,
        title="child",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.messages.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        role="user",
        status="completed",
        content="delegated-child-task",
    )
    profile = ModelContextProfile("openai", "fake", 16_384, False)
    return ContextAssembler(database, store).build(child.session_id, child_turn.turn_id, "openai", "fake", profile=profile)


def _contents(messages):
    return "\n".join(str(message.content) for message in messages)


def test_summary_recent_inherits_only_parent_before_fork_and_keeps_child_task_current(tmp_path: Path) -> None:
    messages = _fixture(tmp_path, "summary_recent", recent_turns=1)
    contents = _contents(messages)

    assert "summary-before-fork" in contents
    assert "parent-task-2" in contents
    assert "parent-task-1" not in contents
    assert "parent-task-3" not in contents
    assert "delegated-child-task\nRepository:" in str(messages[-1].content)
    assert messages[-1].role == "user"


@pytest.mark.parametrize(
    ("mode", "included", "excluded"),
    [
        ("none", (), ("parent-task-1", "parent-task-2", "summary-before-fork")),
        ("recent", ("parent-task-2",), ("parent-task-1", "parent-task-3")),
        ("summary", ("summary-before-fork",), ("parent-task-1", "parent-task-2")),
        ("full", ("parent-task-1", "parent-task-2"), ("parent-task-3",)),
    ],
)
def test_context_fork_modes_are_bounded(tmp_path: Path, mode: str, included: tuple[str, ...], excluded: tuple[str, ...]) -> None:
    contents = _contents(_fixture(tmp_path, mode, recent_turns=1))

    for value in included:
        assert value in contents
    for value in excluded:
        assert value not in contents


def test_context_fork_policy_normalizes_unknown_mode_and_recent_turns() -> None:
    policy = ForkContextPolicy.from_session_metadata(
        {"context_fork": {"mode": "not-a-mode", "recent_turns": 100}}
    )

    assert (policy.mode, policy.recent_turns) == ("summary_recent", 10)


def test_summary_recent_inherits_native_parent_tool_exchange_without_crashing(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    service = SessionService(database)
    parent = service.create_session(tmp_path / "repo", "deepseek", "deepseek/test", "manual")
    parent_turn = store.turns.create_turn(
        session_id=parent.session_id,
        title="parent",
        provider_snapshot="deepseek",
        model_snapshot="deepseek/test",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.messages.create_message(
        session_id=parent.session_id,
        turn_id=parent_turn.turn_id,
        role="user",
        status="completed",
        content="inspect README",
    )
    assistant = store.messages.create_message(
        session_id=parent.session_id,
        turn_id=parent_turn.turn_id,
        role="assistant",
        status="completed",
        content="",
    )
    store.messages.append_message_part(
        assistant.message_id,
        type="tool_call",
        content={
            "provider_tool_call_id": "provider-call-1",
            "tool_name": "read_file",
            "arguments": {"path": "README.md"},
        },
    )
    tool = store.messages.create_message(
        session_id=parent.session_id,
        turn_id=parent_turn.turn_id,
        role="tool",
        status="completed",
        content="ok",
    )
    store.messages.append_message_part(
        tool.message_id,
        type="tool_result",
        content={
            "provider_tool_call_id": "provider-call-1",
            "tool_name": "read_file",
            "content": "README contents",
            "codepilot_tool_call_id": "tool-call-1",
        },
    )

    child = service.create_child_session(
        parent_session_id=parent.session_id,
        forked_from_turn_id=parent_turn.turn_id,
        provider="deepseek",
        model="deepseek/test",
        permission_mode="manual",
        metadata={"context_fork": {"mode": "summary_recent", "recent_turns": 3}},
    )
    child_turn = store.turns.create_turn(
        session_id=child.session_id,
        title="child",
        provider_snapshot="deepseek",
        model_snapshot="deepseek/test",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.messages.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        role="user",
        status="completed",
        content="delegated-child-task",
    )

    messages = ContextAssembler(database, store).build(
        child.session_id,
        child_turn.turn_id,
        "deepseek",
        "deepseek/test",
        profile=ModelContextProfile("deepseek", "deepseek/test", 16_384, False),
    )

    contents = _contents(messages)
    assert '[tool_call] read_file {"path": "README.md"}' in contents
    assert "[tool_result] read_file: README contents" in contents
    assert messages[-1].role == "user"
    assert "delegated-child-task\nRepository:" in messages[-1].content

