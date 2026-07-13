from __future__ import annotations

from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    return SessionStore(database)


def test_create_and_read_core_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create_session(
        project_path=tmp_path / "repo",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
        current_branch="main",
    )
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
    )
    attempt = store.create_attempt(turn_id=turn.turn_id)
    message = store.create_message(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id,
        role="user",
        status="completed",
        content={"text": "hello"},
    )
    part = store.append_message_part(message.message_id, type="text", content="hello")
    tool_call = store.create_tool_call(
        turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id,
        message_id=message.message_id,
        tool_name="read_file",
        arguments={"path": "README.md"},
    )
    tool_result = store.create_tool_result(tool_call_id=tool_call.tool_call_id, status="success", content={"output": "ok"})
    event = store.append_event(session_id=session.session_id, event_type="turn_created", payload={"turn_id": turn.turn_id})

    assert store.get_session(session.session_id).title == "New session"
    assert store.list_turns(session.session_id)[0].turn_id == turn.turn_id
    assert store.list_messages_with_parts(session.session_id, turn.turn_id)[0][0].message_id == message.message_id
    assert store.list_messages_with_parts(session.session_id, turn.turn_id)[0][1][0].part_id == part.part_id
    assert store.list_unresolved_tool_calls(turn.turn_id)[0].tool_call_id == tool_call.tool_call_id
    assert tool_result.status == "success"
    assert event.sequence == 1


def test_session_sorting_and_archive_filter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_session(
        project_path=tmp_path / "repo-a",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )
    second = store.create_session(
        project_path=tmp_path / "repo-b",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )
    store.update_session(first.session_id, last_activity_at="2024-01-01T00:00:00+00:00")
    store.update_session(second.session_id, last_activity_at="2024-01-02T00:00:00+00:00")
    store.archive_session(first.session_id)

    assert [item.session_id for item in store.list_sessions()] == [second.session_id]
    assert [item.session_id for item in store.list_sessions(include_archived=True)] == [first.session_id, second.session_id]


def test_turn_and_attempt_numbers_are_monotonic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create_session(
        project_path=tmp_path / "repo",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )
    first_turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    second_turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 2",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    assert [turn.sequence for turn in store.list_turns(session.session_id)] == [1, 2]
    assert store.create_attempt(turn_id=first_turn.turn_id).attempt_number == 1
    assert store.create_attempt(turn_id=first_turn.turn_id).attempt_number == 2
    assert store.create_attempt(turn_id=second_turn.turn_id).attempt_number == 1


def test_parent_and_fork_fields_can_be_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create_session(
        project_path=tmp_path / "repo",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
        parent_session_id=None,
        forked_from_turn_id=None,
    )

    assert session.parent_session_id is None
    assert session.forked_from_turn_id is None
    assert store.get_session(session.session_id).parent_session_id is None
