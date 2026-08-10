from __future__ import annotations

from pathlib import Path

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.repositories import SessionRepositories
from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver
from codepilot.session.trace_recorder import SessionTraceRecorder
from codepilot.tools.actions import ToolAction
from codepilot.tools.base import ToolResult
from codepilot.tools.registry import TOOL_SPECS


def _session(tmp_path: Path) -> tuple[SessionDatabase, SessionRepositories, str, str, str, str]:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    session = store.sessions.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.turns.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    attempt = store.attempts.create_attempt(turn_id=turn.turn_id)
    call = store.tool_executions.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name="run_shell", arguments={"repo": str(tmp_path), "command": "echo hi"})
    return database, store, session.session_id, turn.turn_id, attempt.attempt_id, call.tool_call_id


def test_trace_recorder_persists_large_tool_result_as_artifact(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, _ = _session(tmp_path)
    recorder = SessionTraceRecorder(database, session_id, turn_id, attempt_id=attempt_id)
    content = "x" * 20_000

    recorder.tool_result_created(
        tool_name="git_diff",
        success=True,
        content=content,
        tool_call_id="call-1",
        provider_tool_call_id="provider-call-1",
    )

    message, parts = store.messages.list_messages_with_parts(session_id, turn_id)[0]
    assert message.content == parts[0].content["content"]
    assert parts[0].artifact_id is not None
    assert len(parts[0].content["content"]) < len(content)
    assert ArtifactStore(database).read_text(parts[0].artifact_id) == content


def test_sqlite_tool_lifecycle_persists_output_preview_and_artifact(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, tool_call_id = _session(tmp_path)
    observer = SQLiteToolLifecycleObserver(database, session_id, turn_id, attempt_id)
    content = "y" * 20_000

    observer.on_execution_finished(tool_call_id, ToolResult(success=True, output=content))

    result = store.tool_executions.get_tool_result_by_call(tool_call_id)
    assert result is not None
    assert result.artifact_id is not None
    assert result.output_preview is not None
    assert len(result.output_preview) < len(content)
    assert result.success is True
    assert ArtifactStore(database).read_text(result.artifact_id) == content


def test_native_tool_call_persists_provider_and_internal_ids_separately(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, _ = _session(tmp_path)
    recorder = SessionTraceRecorder(database, session_id, turn_id, attempt_id=attempt_id)
    observer = SQLiteToolLifecycleObserver(database, session_id, turn_id, attempt_id, message_recorder=recorder)
    provider_tool_call_id = "provider-call-1"
    recorder.assistant_message_started(streaming=True)

    internal_tool_call_id = observer.on_tool_call_created(
        ToolAction(
            tool_name="read_file",
            arguments={"repo": str(tmp_path), "path": "README.md"},
            metadata={"provider_tool_call_id": provider_tool_call_id},
        ),
        TOOL_SPECS["read_file"],
    )
    observer.on_execution_finished(internal_tool_call_id, ToolResult(success=True, output="file output"))
    recorder.tool_result_created(
        tool_name="read_file",
        success=True,
        content="file output",
        observation="file output",
        provider_tool_call_id=provider_tool_call_id,
        tool_call_id=internal_tool_call_id,
    )

    messages = store.messages.list_messages_with_parts(session_id, turn_id)
    assistant_parts = messages[0][1]
    tool_parts = messages[1][1]
    assert assistant_parts[0].content["provider_tool_call_id"] == provider_tool_call_id
    assert assistant_parts[0].metadata["tool_call_id"] == internal_tool_call_id
    assert tool_parts[0].content["provider_tool_call_id"] == provider_tool_call_id
    assert tool_parts[0].content["codepilot_tool_call_id"] == internal_tool_call_id
    assert store.tool_executions.get_tool_call(internal_tool_call_id).metadata["provider_tool_call_id"] == provider_tool_call_id
    assert store.tool_executions.get_tool_call(internal_tool_call_id).message_id == messages[0][0].message_id


def test_interrupted_assistant_call_does_not_persist_message_without_parts(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, _ = _session(tmp_path)
    recorder = SessionTraceRecorder(database, session_id, turn_id, attempt_id=attempt_id)

    recorder.assistant_message_started(streaming=False)
    recorder.assistant_message_interrupted(error="provider failure")

    assert store.messages.list_messages_with_parts(session_id, turn_id) == []


def test_empty_assistant_reply_does_not_persist_message_without_parts(tmp_path: Path) -> None:
    database, store, session_id, turn_id, attempt_id, _ = _session(tmp_path)
    recorder = SessionTraceRecorder(database, session_id, turn_id, attempt_id=attempt_id)

    recorder.assistant_message_started(streaming=False)
    recorder.assistant_message_completed(content="")

    assert store.messages.list_messages_with_parts(session_id, turn_id) == []
