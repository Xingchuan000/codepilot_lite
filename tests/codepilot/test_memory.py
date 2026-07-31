from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.memory.instructions import ProjectInstructionLoader
from codepilot.memory.repository import CandidateRepository
from codepilot.memory.service import MemoryService
from codepilot.session.compaction import CompactionService
from codepilot.session.context import ContextAssembler
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def _database(tmp_path: Path) -> SessionDatabase:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    return database


def test_project_memory_is_shared_by_project_isolated_and_versioned(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = SessionStore(database)
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_session = store.create_session(project_path=first_project, provider="openai", current_model="fake", permission_mode="manual")
    another_session = store.create_session(project_path=first_project, provider="openai", current_model="fake", permission_mode="manual")
    isolated_session = store.create_session(project_path=second_project, provider="openai", current_model="fake", permission_mode="manual")
    service = MemoryService(database)

    old = service.add(first_session.project_id, "command", "test:unit", "pytest -q")
    new = service.add(another_session.project_id, "command", "test:unit", "pytest tests -q")

    assert service.memories.get(old.memory_id).status == "superseded"
    assert service.list(first_session.project_id) == [new]
    assert service.search(another_session.project_id, "pytest")[0].memory == new
    assert service.search(isolated_session.project_id, "pytest") == []


def test_instruction_loader_reuses_sha_and_reloads_changed_file(tmp_path: Path) -> None:
    database = _database(tmp_path)
    project = SessionStore(database).create_project(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text("Use pytest.")
    loader = ProjectInstructionLoader(database)

    first = loader.load(project.project_id, tmp_path)[0]
    cached = loader.load(project.project_id, tmp_path)[0]
    agents.write_text("Use pytest -q.")
    changed = loader.load(project.project_id, tmp_path)[0]

    assert cached.instruction_id == first.instruction_id
    assert changed.sha256 != first.sha256
    assert changed.content["text"] == "Use pytest -q."


def test_project_instructions_enter_context_as_bounded_system_items(tmp_path: Path) -> None:
    database = _database(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Always run pytest.")
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="instructions",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="Continue")

    context = ContextAssembler(database).build(session.session_id, turn.turn_id, "openai", "fake")

    assert any(message.role == "system" and "Always run pytest." in message.content for message in context)


def test_structured_summary_is_mandatory_when_it_covers_history(tmp_path: Path) -> None:
    database = _database(tmp_path)
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
        for index in range(6)
    ]
    for turn in turns:
        store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content=f"task {turn.sequence}")

    summary = CompactionService(database).compact(session.session_id, force=True, current_turn_id=turns[-1].turn_id).summary
    plan = ContextAssembler(database).build_plan(session.session_id, turns[-1].turn_id, "openai", "fake")

    assert isinstance(summary.content, dict)
    assert set(summary.content) >= {"task_goal", "user_constraints", "test_results", "unresolved_work", "source_message_ids"}
    assert next(item for item in plan.summary_items if item.key == f"summary-{summary.summary_id}").mandatory is True


def test_candidate_requires_approval_and_secret_is_rejected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="memory",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    candidate = CandidateRepository(database).create(
        session.project_id,
        session.session_id,
        turn.turn_id,
        "convention",
        "style:pathlib",
        {"text": "Use pathlib."},
        {"message_ids": []},
        0.9,
    )
    service = MemoryService(database)

    assert service.list(session.project_id) == []
    assert service.approve(candidate.candidate_id).content == {"text": "Use pathlib."}
    with pytest.raises(ValueError, match="secret"):
        service.add(session.project_id, "project_preference", "credential", "API_KEY=top-secret-value")
