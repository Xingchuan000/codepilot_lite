from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.llm.fake import FakeLLMClient
from codepilot.memory.models import SessionSummaryContent
from codepilot.memory.summarizer import LLMSummaryGenerator, SessionSummaryEvidence, merge_llm_summary
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


def test_deterministic_summary_redacts_secret_without_changing_original_message(tmp_path: Path) -> None:
    database, store, session, turns, _ = _history_with_tool_facts(tmp_path)
    secret = "top-secret-value"
    message = store.create_message(
        session_id=session.session_id,
        turn_id=turns[0].turn_id,
        role="user",
        status="completed",
        content=f"API_KEY={secret}",
    )

    summary = CompactionService(database, LLMSummaryGenerator(FakeLLMClient(["not json"]))).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    ).summary.content

    assert secret not in str(summary)
    assert "[REDACTED_SECRET]" in str(summary)
    assert next(item for item, _ in store.list_messages_with_parts(session.session_id) if item.message_id == message.message_id).content == f"API_KEY={secret}"


def test_summary_redacts_secrets_from_commands_tool_results_and_errors(tmp_path: Path) -> None:
    database, store, session, turns, _ = _history_with_tool_facts(tmp_path)
    call = store.create_tool_call(
        turn_id=turns[0].turn_id,
        tool_name="run_tests",
        arguments={"command": "TOKEN=command-secret pytest"},
    )
    store.persist_tool_result(
        call.tool_call_id,
        call_status="failed",
        result_status="failed",
        content="failed",
        output_preview="PASSWORD=preview-secret",
        error="API_KEY=error-secret",
        success=False,
    )
    preview_call = store.create_tool_call(
        turn_id=turns[0].turn_id,
        tool_name="run_tests",
        arguments={"command": "pytest preview"},
    )
    store.persist_tool_result(
        preview_call.tool_call_id,
        call_status="failed",
        result_status="failed",
        content="failed",
        output_preview="PASSWORD=preview-secret",
        success=False,
    )

    summary = CompactionService(database).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    ).summary.content

    assert all(secret not in str(summary) for secret in ("command-secret", "preview-secret", "error-secret"))
    assert "[REDACTED_SECRET]" in str(summary)


def test_llm_summary_receives_only_redacted_evidence(tmp_path: Path) -> None:
    database, store, session, turns, _ = _history_with_tool_facts(tmp_path)
    store.create_message(
        session_id=session.session_id,
        turn_id=turns[0].turn_id,
        role="user",
        status="completed",
        content="password: provider-secret",
    )
    llm = FakeLLMClient([json.dumps(SessionSummaryContent(task_goal="Continue").to_dict())])

    CompactionService(database, LLMSummaryGenerator(llm)).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    )

    request = str(llm.calls)
    assert "provider-secret" not in request
    assert "[REDACTED_SECRET]" in request


def test_cumulative_compaction_scrubs_unsafe_previous_summary_and_audits_counts(tmp_path: Path) -> None:
    database, store, session, turns, messages = _history_with_tool_facts(tmp_path)
    secret = "legacy-secret"
    unsafe = SessionSummaryContent(task_goal=f"API_KEY={secret}").to_dict()
    old = store.replace_context_summary(
        session_id=session.session_id,
        previous_summary_id=None,
        summary_content=unsafe,
        turn_id=turns[0].turn_id,
        source_start_sequence=turns[0].sequence,
        source_end_sequence=turns[0].sequence,
        model="old",
        metadata={"covered_message_ids": [messages[0].message_id]},
        event_payload={},
    )
    llm = FakeLLMClient([json.dumps(SessionSummaryContent(task_goal="Continue").to_dict())])

    result = CompactionService(database, LLMSummaryGenerator(llm)).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    )

    assert secret not in str(result.summary.content)
    assert secret not in str(llm.calls)
    assert store.list_context_summaries(session.session_id)[0].status == "superseded"
    assert old.summary_id != result.summary.summary_id
    event = next(event for event in store.list_events(session.session_id) if event.event_type == "context_summary_redacted")
    assert set(event.payload) == {"redaction_count", "message_count"}
    assert secret not in str(event.payload)


def test_merge_llm_summary_treats_empty_work_lists_as_current_state() -> None:
    previous = SessionSummaryContent(
        confirmed_decisions=("keep SQLite",),
        unresolved_work=("fix A",),
        next_actions=("test A",),
    )
    proposed = SessionSummaryContent(task_goal="Done", unresolved_work=(), next_actions=())
    evidence = SessionSummaryEvidence((), (), (), (), (), (), {}, "", "")

    merged = merge_llm_summary(previous, proposed, evidence)

    assert merged.unresolved_work == ()
    assert merged.next_actions == ()
    assert merged.confirmed_decisions == ("keep SQLite",)


class _FailingSummaryLLM:
    def complete(self, messages):
        raise RuntimeError("provider failed: API_KEY=event-secret")


def test_summary_fallback_event_redacts_provider_exception(tmp_path: Path) -> None:
    database, store, session, turns, _ = _history_with_tool_facts(tmp_path)

    CompactionService(database, LLMSummaryGenerator(_FailingSummaryLLM())).compact(
        session.session_id,
        force=True,
        current_turn_id=turns[-1].turn_id,
    )

    event = next(event for event in store.list_events(session.session_id) if event.event_type == "context_summary_fallback_used")
    assert event.payload == {
        "error_type": "RuntimeError",
        "error": "provider failed: API_KEY=[REDACTED_SECRET]",
        "message_count": 2,
    }
    assert "event-secret" not in str(event.payload)


def test_compaction_failed_event_redacts_summarizer_exception(tmp_path: Path) -> None:
    database, store, session, turns, _ = _history_with_tool_facts(tmp_path)

    def fail(_):
        raise RuntimeError("summary failed: TOKEN=failure-secret")

    with pytest.raises(RuntimeError, match="failure-secret"):
        CompactionService(database, fail).compact(
            session.session_id,
            force=True,
            current_turn_id=turns[-1].turn_id,
        )

    event = next(event for event in store.list_events(session.session_id) if event.event_type == "context_compaction_failed")
    assert event.payload == {
        "error_type": "RuntimeError",
        "error": "summary failed: TOKEN=[REDACTED_SECRET]",
        "message_count": 2,
    }
    assert "failure-secret" not in str(event.payload)


def test_compaction_failed_event_redacts_persistence_exception(tmp_path: Path) -> None:
    database, store, session, turns, _ = _history_with_tool_facts(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_summary_insert BEFORE INSERT ON context_summaries "
            "BEGIN SELECT RAISE(ABORT, 'API_KEY=persistence-secret'); END"
        )

    with pytest.raises(Exception, match="persistence-secret"):
        CompactionService(database).compact(
            session.session_id,
            force=True,
            current_turn_id=turns[-1].turn_id,
        )

    event = next(event for event in store.list_events(session.session_id) if event.event_type == "context_compaction_failed")
    assert event.payload["error_type"] == "IntegrityError"
    assert event.payload["error"] == "API_KEY=[REDACTED_SECRET]"
    assert event.payload["message_count"] == 2
    assert "persistence-secret" not in str(event.payload)
