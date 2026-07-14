from pathlib import Path

from codepilot.session.compaction import CompactionService
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def test_second_compact_keeps_first_covered_message_ids(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=Path(tmp_path), provider="openai", current_model="fake", permission_mode="manual")
    turns = [store.create_turn(session_id=session.session_id, title=str(index), provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None) for index in range(10)]
    messages = [store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content=f"message-{turn.sequence}") for turn in turns]
    service = CompactionService(database, summarizer=lambda _: "Key decisions\nFiles/tests/diff\nUnfinished work")

    first = service.compact(session.session_id, force=True, current_turn_id=turns[-1].turn_id)
    later_turns = [store.create_turn(session_id=session.session_id, title=str(index), provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None) for index in range(5)]
    extra = store.create_message(session_id=session.session_id, turn_id=later_turns[0].turn_id, role="user", status="completed", content="new-message")
    second = service.compact(session.session_id, force=True, current_turn_id=later_turns[-1].turn_id)

    assert set(first.covered_message_ids) <= set(second.summary.metadata["covered_message_ids"])
    assert extra.message_id in second.summary.metadata["covered_message_ids"]
    assert store.list_context_summaries(session.session_id)[0].status == "superseded"
