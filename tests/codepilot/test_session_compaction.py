from __future__ import annotations

from pathlib import Path

from codepilot.session.compaction import CompactionService
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def test_force_compact_creates_summary_message_and_keeps_recent_turn(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turns = [store.create_turn(session_id=session.session_id, title=f"Turn {index}", provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None) for index in range(6)]
    for item in turns:
        store.create_message(session_id=session.session_id, turn_id=item.turn_id, role="user", status="completed", content=f"Turn {item.sequence}")
    turn = turns[-1]

    result = CompactionService(database, summarizer=lambda _: "Key decisions\nFiles/tests/diff\nUnfinished work").compact(session.session_id, force=True, current_turn_id=turn.turn_id)
    messages = store.list_messages_with_parts(session.session_id)

    assert result.covered_message_ids
    assert messages[-1][0].metadata["summary_id"] == result.summary.summary_id
