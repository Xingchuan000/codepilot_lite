from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codepilot.memory.instructions import ProjectInstructionLoader
from codepilot.memory.models import MemoryQuery
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


def _candidate(database: SessionDatabase, tmp_path: Path, key: str = "style:pathlib"):
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
    return session, CandidateRepository(database).create(
        session.project_id,
        session.session_id,
        turn.turn_id,
        "convention",
        key,
        {"text": "Use pathlib."},
        {"message_ids": []},
        0.9,
    )


def test_repeated_candidate_approval_does_not_create_memory_version(tmp_path: Path) -> None:
    database = _database(tmp_path)
    session, candidate = _candidate(database, tmp_path)
    service = MemoryService(database)
    first = service.approve(candidate.candidate_id)

    with pytest.raises(ValueError, match="already been decided"):
        service.approve(candidate.candidate_id)

    assert service.list(session.project_id) == [first]
    assert service.candidates.get(candidate.candidate_id).status == "accepted"


def test_candidate_approval_rolls_back_memory_when_status_update_fails(tmp_path: Path) -> None:
    database = _database(tmp_path)
    session, candidate = _candidate(database, tmp_path)
    old = MemoryService(database).add(session.project_id, "convention", candidate.canonical_key, "Use os.path.")
    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_candidate_accept BEFORE UPDATE OF status ON memory_candidates "
            "WHEN NEW.status = 'accepted' BEGIN SELECT RAISE(ABORT, 'forced candidate failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced candidate failure"):
        MemoryService(database).approve(candidate.candidate_id)

    assert MemoryService(database).list(session.project_id) == [old]
    assert CandidateRepository(database).get(candidate.candidate_id).status == "pending"


def test_unknown_candidate_approval_does_not_create_memory(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(LookupError):
        MemoryService(database).approve("candidate-missing")
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_memories").fetchone()[0] == 0


def test_memory_search_supports_cjk_paths_and_branch_isolation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    service = MemoryService(database)
    global_memory = service.memories.add(
        session.project_id,
        "architecture",
        "facts:sqlite",
        "事实来源",
        {"text": "所有事实来源都是SQLite，JSON只用于导出"},
    )
    path_memory = service.memories.add(
        session.project_id,
        "file_map",
        "file:src/a.py",
        "A module",
        {"text": "核心模块", "paths": ["src/a.py"]},
    )
    branch_memory = service.memories.add(
        session.project_id,
        "known_issue",
        "issue:feature",
        "Feature issue",
        {"text": "Only on feature"},
        branch_scope="feature/x",
    )
    other_memory = service.memories.add(
        session.project_id,
        "known_issue",
        "issue:other",
        "Other issue",
        {"text": "Only on other"},
        branch_scope="feature/y",
    )

    assert service.search(session.project_id, "事实来源")[0].memory == global_memory
    assert service.search(session.project_id, "导出")[0].memory == global_memory
    assert [result.memory for result in service.memories.search(session.project_id, MemoryQuery("", paths=("src/a.py",)))] == [path_memory]
    assert branch_memory not in [result.memory for result in service.memories.search(session.project_id, MemoryQuery("issue", branch=None))]
    current = [result.memory for result in service.memories.search(session.project_id, MemoryQuery("issue", branch="feature/x"))]
    assert branch_memory in current
    assert other_memory not in current
