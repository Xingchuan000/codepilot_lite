from __future__ import annotations

from pathlib import Path

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore
from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver
from codepilot.session.trace_recorder import SessionTraceRecorder
from codepilot.tools.base import ToolResult


def _session(tmp_path: Path) -> tuple[SessionDatabase, SessionStore, str, str, str, str]:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
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
    call = store.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name="run_shell", arguments={"repo": str(tmp_path), "command": "echo hi"})
    return database, store, session.session_id, turn.turn_id, attempt.attempt_id, call.tool_call_id


def test_trace_recorder_persists_large_tool_result_as_artifact(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, _ = _session(tmp_path)
    recorder = SessionTraceRecorder(database, session_id, turn_id, attempt_id=attempt_id)
    content = "x" * 20_000

    recorder.tool_result_created(tool_name="git_diff", success=True, content=content, tool_call_id="call-1")

    message, parts = store.list_messages_with_parts(session_id, turn_id)[0]
    assert message.content == parts[0].content
    assert parts[0].artifact_id is not None
    assert len(parts[0].content) < len(content)
    assert ArtifactStore(database).read_text(parts[0].artifact_id) == content


def test_sqlite_tool_lifecycle_persists_output_preview_and_artifact(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, tool_call_id = _session(tmp_path)
    observer = SQLiteToolLifecycleObserver(database, session_id, turn_id, attempt_id)
    content = "y" * 20_000

    observer.on_execution_finished(tool_call_id, ToolResult(success=True, output=content))

    result = store.get_tool_result_by_call(tool_call_id)
    assert result is not None
    assert result.artifact_id is not None
    assert result.output_preview is not None
    assert len(result.output_preview) < len(content)
    assert result.success is True
    assert ArtifactStore(database).read_text(result.artifact_id) == content
