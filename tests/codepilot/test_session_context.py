from __future__ import annotations

from pathlib import Path

from codepilot.llm.types import ChatMessage
from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def test_context_assembler_replays_current_turn_and_interruption_notice(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    repo = tmp_path / "repo"
    session = store.create_session(project_path=repo, provider="openai", current_model="gpt-4.1", permission_mode="manual")
    previous_turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
    )
    current_turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 2",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
    )
    store.create_message(session_id=session.session_id, turn_id=previous_turn.turn_id, attempt_id=None, role="user", status="completed", content="old task")
    store.create_message(session_id=session.session_id, turn_id=current_turn.turn_id, attempt_id=None, role="user", status="completed", content="请继续")
    store.append_event(session_id=session.session_id, event_type="branch_changed", payload={"old_branch": "main", "new_branch": "feature"})
    assistant = store.create_message(session_id=session.session_id, turn_id=current_turn.turn_id, attempt_id=None, role="assistant", status="interrupted", content="partial answer")

    context = ContextAssembler(database, store).build(session.session_id, current_turn.turn_id, "openai", "gpt-4.1")

    assert any(isinstance(item, ChatMessage) and item.role == "user" and item.content.endswith(f"Repository: {repo.resolve()}") for item in context)
    assert any(isinstance(item, ChatMessage) and item.role == "assistant" and "The previous assistant response was interrupted." in item.content for item in context)
    assert any(isinstance(item, ChatMessage) and item.role == "system" and "Session event: branch_changed" in item.content for item in context)
    assert assistant.status == "interrupted"
