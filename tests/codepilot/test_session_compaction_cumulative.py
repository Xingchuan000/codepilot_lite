import json
from pathlib import Path

from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import LLMResponse
from codepilot.memory.models import SessionSummaryContent
from codepilot.memory.summarizer import LLMSummaryGenerator
from codepilot.session.compaction import CompactionService
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def test_second_compact_keeps_first_covered_message_ids(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=Path(tmp_path), provider="openai", current_model="fake", permission_mode="manual")
    turns = [store.create_turn(session_id=session.session_id, title=str(index), provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None) for index in range(10)]
    for turn in turns:
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content=f"message-{turn.sequence}")
    service = CompactionService(database, summarizer=lambda _: "Key decisions\nFiles/tests/diff\nUnfinished work")

    first = service.compact(session.session_id, force=True, current_turn_id=turns[-1].turn_id)
    later_turns = [store.create_turn(session_id=session.session_id, title=str(index), provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None) for index in range(5)]
    extra = store.create_message(session_id=session.session_id, turn_id=later_turns[0].turn_id, role="user", status="completed", content="new-message")
    second = service.compact(session.session_id, force=True, current_turn_id=later_turns[-1].turn_id)

    assert set(first.covered_message_ids) <= set(second.summary.metadata["covered_message_ids"])
    assert extra.message_id in second.summary.metadata["covered_message_ids"]
    assert store.list_context_summaries(session.session_id)[0].status == "superseded"


def test_successful_cumulative_summary_replaces_resolved_work_but_keeps_facts(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
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
        for index in range(10)
    ]
    first_messages = [
        store.create_message(
            session_id=session.session_id,
            turn_id=turn.turn_id,
            role="user",
            status="completed",
            content=f"message-{index}",
        )
        for index, turn in enumerate(turns)
    ]
    call = store.create_tool_call(turn_id=turns[0].turn_id, tool_name="run_shell", arguments={"command": "pytest A"})
    store.persist_tool_result(
        call.tool_call_id,
        call_status="completed",
        result_status="success",
        content="passed",
        success=True,
    )
    first_value = SessionSummaryContent(
        task_goal="Fix A",
        confirmed_decisions=("use SQLite",),
        unresolved_work=("fix A",),
        next_actions=("run A tests",),
    ).to_dict()
    second_value = SessionSummaryContent(task_goal="A complete", unresolved_work=(), next_actions=()).to_dict()
    service = CompactionService(
        database,
        LLMSummaryGenerator(StructuredFakeLLM([LLMResponse(content=json.dumps(first_value)), LLMResponse(content=json.dumps(second_value))])),
    )
    first = service.compact(session.session_id, force=True, current_turn_id=turns[-1].turn_id)
    later_turns = [
        store.create_turn(
            session_id=session.session_id,
            title=f"later-{index}",
            provider_snapshot="openai",
            model_snapshot="fake",
            permission_mode_snapshot="manual",
            branch_snapshot="main",
        )
        for index in range(5)
    ]
    new_message = store.create_message(
        session_id=session.session_id,
        turn_id=later_turns[0].turn_id,
        role="user",
        status="completed",
        content="A is fixed",
    )

    second = service.compact(session.session_id, force=True, current_turn_id=later_turns[-1].turn_id)

    assert second.summary.content["unresolved_work"] == []
    assert second.summary.content["next_actions"] == []
    assert second.summary.content["confirmed_decisions"] == ["use SQLite"]
    assert second.summary.content["commands_run"] == ["pytest A"]
    assert set(first.summary.content["source_message_ids"]) <= set(second.summary.content["source_message_ids"])
    assert new_message.message_id in second.summary.content["source_message_ids"]
    assert first_messages[0].message_id in second.summary.content["source_message_ids"]


def test_failed_cumulative_llm_summary_preserves_previous_work_snapshot(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
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
            branch_snapshot=None,
        )
        for index in range(10)
    ]
    for index, turn in enumerate(turns):
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content=str(index))
    value = SessionSummaryContent(unresolved_work=("fix A",), next_actions=("test A",)).to_dict()
    service = CompactionService(
        database,
        LLMSummaryGenerator(StructuredFakeLLM([LLMResponse(content=json.dumps(value)), LLMResponse(content="not json")])),
    )
    service.compact(session.session_id, force=True, current_turn_id=turns[-1].turn_id)
    later_turns = [
        store.create_turn(
            session_id=session.session_id,
            title=f"later-{index}",
            provider_snapshot="openai",
            model_snapshot="fake",
            permission_mode_snapshot="manual",
            branch_snapshot=None,
        )
        for index in range(5)
    ]
    store.create_message(session_id=session.session_id, turn_id=later_turns[0].turn_id, role="user", status="completed", content="continue")

    summary = service.compact(session.session_id, force=True, current_turn_id=later_turns[-1].turn_id).summary.content

    assert summary["unresolved_work"] == ["fix A"]
    assert summary["next_actions"] == ["test A"]
