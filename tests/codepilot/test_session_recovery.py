from __future__ import annotations

import hashlib
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.recovery import RecoveryService
from codepilot.session.store import SessionStore


def _records(tmp_path: Path, tool_name: str, arguments: dict, token: dict):
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="running",
    )
    attempt = store.create_attempt(turn_id=turn.turn_id, status="running", started_at="2024-01-01T00:00:00+00:00")
    call = store.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name=tool_name, arguments=arguments)
    store.persist_tool_execution_started(call.tool_call_id, token)
    return database, store, session, turn, attempt, call


def test_recover_completed_call_and_in_progress_message_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    after = b"changed\n"
    path.write_bytes(after)
    database, store, session, turn, attempt, call = _records(
        tmp_path,
        "replace_range",
        {"repo": str(tmp_path), "path": "sample.txt", "start_line": 1, "end_line": 1, "replacement": "changed\n"},
        {"path": str(path), "pre_file_sha256": hashlib.sha256(b"old\n").hexdigest(), "expected_file_sha256": hashlib.sha256(after).hexdigest()},
    )
    message = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=attempt.attempt_id, role="assistant", status="in_progress", content="partial")
    recovery = RecoveryService(database)

    first = recovery.recover_session(session.session_id)
    second = recovery.recover_session(session.session_id)

    assert store.get_tool_call(call.tool_call_id).status == "completed"
    assert store.get_tool_result_by_call(call.tool_call_id).status == "recovered_completed"
    messages = store.list_messages_with_parts(session.session_id)
    assert messages[0][0].message_id == message.message_id
    assert messages[0][0].status == "interrupted"
    assert any(item.role == "system" and item.metadata.get("recovery_status") == "recovered_completed" for item, _ in messages)
    context = ContextAssembler(database, store).build(session.session_id, turn.turn_id, "openai", "fake")
    assert any("recovered_completed" in item.content and "replace_range" in item.content and "sample.txt" in item.content for item in context)
    assert len(first.resumable_attempt_ids) == 1
    assert second.resumable_attempt_ids == first.resumable_attempt_ids
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tool_results WHERE tool_call_id = ?", (call.tool_call_id,)).fetchone()[0] == 1


def test_unknown_call_requires_user_and_retry_preserves_original_call(tmp_path: Path) -> None:
    command = "git commit -m x"
    database, store, session, turn, _, call = _records(
        tmp_path,
        "run_shell",
        {"repo": str(tmp_path), "command": command},
        {"command_sha256": hashlib.sha256(command.encode()).hexdigest(), "auto_retry_allowed": False},
    )
    recovery = RecoveryService(database)

    plan = recovery.recover_session(session.session_id)
    repeated = recovery.recover_session(session.session_id)
    attempt = recovery.resolve_unknown(call.tool_call_id, "retry")

    assert plan.unresolved_tool_call_ids == (call.tool_call_id,)
    assert repeated.unresolved_tool_call_ids == (call.tool_call_id,)
    assert store.get_tool_call(call.tool_call_id).status == "execution_uncertain"
    assert store.get_tool_result_by_call(call.tool_call_id).status == "execution_uncertain"
    assert attempt is not None
    assert attempt.turn_id == turn.turn_id
    assert store.get_turn(turn.turn_id).status == "queued"
    assert sum(event.event_type == "tool_reconciled" for event in store.list_events(session.session_id)) == 1


def test_active_worker_is_not_recovered_and_created_attempt_is_rescheduled(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    attempt = store.create_attempt(turn_id=turn.turn_id)
    store.start_turn_attempt(
        turn.turn_id,
        attempt.attempt_id,
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )

    active = RecoveryService(database).recover_session(session.session_id)

    assert active.interrupted_turn_ids == ()
    assert active.resumable_attempt_ids == ()
    assert store.get_attempt(attempt.attempt_id).status == "running"

    with database.transaction() as connection:
        connection.execute(
            "UPDATE run_attempts SET status = 'created', worker_id = NULL, lease_expires_at = NULL WHERE attempt_id = ?",
            (attempt.attempt_id,),
        )
        connection.execute("UPDATE turns SET status = 'queued' WHERE turn_id = ?", (turn.turn_id,))
    queued = RecoveryService(database).recover_session(session.session_id)
    assert queued.resumable_attempt_ids == (attempt.attempt_id,)


def test_abort_closes_every_uncertain_call_in_turn(tmp_path: Path) -> None:
    first_command = "git commit -m first"
    database, store, session, turn, attempt, first = _records(
        tmp_path,
        "run_shell",
        {"repo": str(tmp_path), "command": first_command},
        {"command_sha256": hashlib.sha256(first_command.encode()).hexdigest(), "auto_retry_allowed": False},
    )
    second_command = "git reset --hard"
    second = store.create_tool_call(
        turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id,
        tool_name="run_shell",
        arguments={"repo": str(tmp_path), "command": second_command},
    )
    store.persist_tool_execution_started(
        second.tool_call_id,
        {"command_sha256": hashlib.sha256(second_command.encode()).hexdigest(), "auto_retry_allowed": False},
    )
    recovery = RecoveryService(database)
    recovery.recover_session(session.session_id)

    recovery.resolve_unknown(first.tool_call_id, "abort")

    assert store.get_turn(turn.turn_id).status == "cancelled"
    assert store.get_tool_call(first.tool_call_id).status == "recovery_aborted"
    assert store.get_tool_call(second.tool_call_id).status == "recovery_aborted"
    assert store.get_tool_result_by_call(first.tool_call_id).status == "recovery_aborted"
    assert store.get_tool_result_by_call(second.tool_call_id).status == "recovery_aborted"
    assert recovery.inspect_session(session.session_id).unresolved_tool_call_ids == ()


def test_pending_approval_blocks_automatic_recovery_attempt(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="running",
    )
    attempt = store.create_attempt(turn_id=turn.turn_id, status="running", started_at="2024-01-01T00:00:00+00:00")
    call = store.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name="run_shell", arguments={"repo": str(tmp_path), "command": "echo hi"}, status="approval_pending")
    store.create_permission_request(
        request_id="permission-1",
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id,
        tool_call_id=call.tool_call_id,
        tool_name="run_shell",
        arguments={"command": "echo hi"},
        reason="approval required",
        status="pending",
    )

    plan = RecoveryService(database).recover_session(session.session_id)

    assert plan.pending_approval_request_ids == ("permission-1",)
    assert plan.resumable_attempt_ids == ()
    assert store.get_turn(turn.turn_id).status == "running"


def test_approval_after_restart_finalizes_old_tool_call_as_not_executed(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="running",
    )
    attempt = store.create_attempt(turn_id=turn.turn_id, status="running", started_at="2024-01-01T00:00:00+00:00")
    call = store.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name="run_shell", arguments={"command": "echo hi"}, status="approval_pending")
    store.create_permission_request(
        request_id="permission-restart-1",
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id,
        tool_call_id=call.tool_call_id,
        tool_name="run_shell",
        arguments={"command": "echo hi"},
        reason="approval required",
        status="pending",
    )
    store.persist_permission_resolution(
        "permission-restart-1",
        "approve_once",
        "approved after restart",
        create_grant=False,
        source="test",
    )

    resumed = RecoveryService(database).resume_after_permission("permission-restart-1")

    assert resumed is not None
    assert store.get_tool_call(call.tool_call_id).status == "recovered_not_executed"
    assert store.get_tool_result_by_call(call.tool_call_id).status == "recovered_not_executed"


def test_recovery_claims_only_one_interrupted_turn_at_a_time(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turns = [
        store.create_turn(
            session_id=session.session_id,
            title=f"Turn {index}",
            provider_snapshot="openai",
            model_snapshot="fake",
            permission_mode_snapshot="manual",
            branch_snapshot=None,
            status="interrupted",
        )
        for index in (1, 2)
    ]
    recovery = RecoveryService(database)

    first = recovery.recover_session(session.session_id)

    assert len(first.resumable_attempt_ids) == 1
    assert store.get_attempt(first.resumable_attempt_ids[0]).turn_id == turns[0].turn_id
    assert store.get_turn(turns[1].turn_id).status == "interrupted"
