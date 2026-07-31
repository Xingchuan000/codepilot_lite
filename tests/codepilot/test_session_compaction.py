from __future__ import annotations

import json
from pathlib import Path

from codepilot.llm.fake import FakeLLMClient
from codepilot.memory.models import SessionSummaryContent
from codepilot.memory.summarizer import LLMSummaryGenerator
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


def _history_with_tool_facts(tmp_path: Path):
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turns = [
        store.create_turn(
            session_id=session.session_id,
            title=str(index),
            provider_snapshot="openai",
            model_snapshot="fake",
            permission_mode_snapshot="manual",
            branch_snapshot="main",
        )
        for index in range(6)
    ]
    messages = [
        store.create_message(
            session_id=session.session_id,
            turn_id=turn.turn_id,
            role="user",
            status="completed",
            content="SQLite is the source of truth" if index == 0 else f"task {index}",
        )
        for index, turn in enumerate(turns)
    ]
    test_call = store.create_tool_call(turn_id=turns[0].turn_id, tool_name="run_tests", arguments={"command": "pytest -q"})
    store.persist_tool_result(
        test_call.tool_call_id,
        call_status="completed",
        result_status="success",
        content="10 passed",
        output_preview="10 passed",
        success=True,
    )
    write_call = store.create_tool_call(turn_id=turns[0].turn_id, tool_name="apply_patch", arguments={"path": "src/a.py"})
    store.persist_tool_result(
        write_call.tool_call_id,
        call_status="completed",
        result_status="success",
        content="done",
        success=True,
    )
    return database, store, session, turns, messages


def test_deterministic_summary_contains_sqlite_tool_facts(tmp_path: Path) -> None:
    database, _, session, turns, messages = _history_with_tool_facts(tmp_path)

    summary = CompactionService(database).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    ).summary.content

    assert "pytest -q" in summary["commands_run"]
    assert "src/a.py" in summary["files_modified"]
    assert any("success" in result and "10 passed" in result for result in summary["test_results"])
    assert messages[0].message_id in summary["source_message_ids"]


def test_invalid_llm_summary_uses_deterministic_fallback_and_keeps_messages(tmp_path: Path) -> None:
    database, store, session, turns, messages = _history_with_tool_facts(tmp_path)

    result = CompactionService(database, LLMSummaryGenerator(FakeLLMClient(["not json"]))).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    )

    assert result.summary.content["commands_run"] == ["pytest -q"]
    assert any(event.event_type == "context_summary_fallback_used" for event in store.list_events(session.session_id))
    assert {message.message_id for message, _ in store.list_messages_with_parts(session.session_id)} >= {
        message.message_id for message in messages
    }


def test_llm_cannot_add_unobserved_tool_facts(tmp_path: Path) -> None:
    database, _, session, turns, _ = _history_with_tool_facts(tmp_path)
    hallucinated = SessionSummaryContent(
        task_goal="Continue",
        files_modified=("src/fake.py",),
        test_results=("999 passed",),
    ).to_dict()

    summary = CompactionService(
        database,
        LLMSummaryGenerator(FakeLLMClient([json.dumps(hallucinated)])),
    ).compact(session.session_id, force=True, current_turn_id=turns[-1].turn_id).summary.content

    assert summary["files_modified"] == ["src/a.py"]
    assert all("999 passed" not in result for result in summary["test_results"])
