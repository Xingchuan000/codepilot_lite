from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codepilot.memory.context_provider import MemoryContextProvider
from codepilot.memory.instruction_budget import TRUNCATION_MARKER, resolve_instruction_budget, truncate_text_to_tokens
from codepilot.memory.instructions import ProjectInstructionLoader
from codepilot.memory.models import MemoryQuery
from codepilot.memory.policy import REDACTED_SECRET, is_memory_content_safe, redact_memory_value
from codepilot.memory.repository import CandidateRepository
from codepilot.memory.service import MemoryService
from codepilot.session.compaction import CompactionService
from codepilot.session.context import ContextAssembler
from codepilot.session.context_budget import estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
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
    assert all(TRUNCATION_MARKER not in message.content for message in context)


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


def test_memory_redaction_is_recursive_idempotent_and_non_mutating() -> None:
    original = {
        "text": "API_KEY=top-secret-value",
        "nested": ["password: abc123", ("ghp_abcdefghijklmnopqrstuvwxyz",)],
    }

    first = redact_memory_value(original)
    second = redact_memory_value(first.value)

    assert first.redaction_count == 3
    assert second.redaction_count == 0
    assert second.value == first.value
    assert REDACTED_SECRET in str(first.value)
    assert is_memory_content_safe(first.value)
    assert original["text"] == "API_KEY=top-secret-value"


def test_instruction_loader_hashes_bytes_beyond_preview_limit(tmp_path: Path) -> None:
    database = _database(tmp_path)
    project = SessionStore(database).create_project(tmp_path)
    path = tmp_path / "AGENTS.md"
    path.write_text("unchanged-tail-a")
    loader = ProjectInstructionLoader(database, max_bytes=9)
    first = loader.load(project.project_id, tmp_path)[0]
    path.write_text("unchanged-tail-b")

    changed = loader.load(project.project_id, tmp_path)[0]

    assert changed.instruction_id != first.instruction_id
    assert changed.sha256 != first.sha256
    assert changed.content == {
        "text": "unchanged",
        "truncated": True,
        "source_size_bytes": 16,
        "loaded_bytes": 9,
    }


def test_large_project_instructions_share_model_budget_and_keep_sources(tmp_path: Path) -> None:
    database = _database(tmp_path)
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="test", current_model="small", permission_mode="manual")
    (tmp_path / "AGENTS.md").write_text("agents-head\n" + "a" * 31_000 + "\nagents-tail")
    (tmp_path / "CLAUDE.md").write_text("claude-head\n" + "c" * 31_000 + "\nclaude-tail")
    profile = ModelContextProfile("test", "small", 8_192, False)

    items = MemoryContextProvider(database).instruction_items(session.project_id, tmp_path, profile)

    assert sum(item.estimated_tokens for item in items) <= resolve_instruction_budget(profile).total_tokens
    assert len(items) == 1
    assert items[0].key == "project-instructions"
    assert items[0].mandatory is True
    content = items[0].messages[0].content
    assert "Source: AGENTS.md" in content
    assert "Source: CLAUDE.md" in content
    assert TRUNCATION_MARKER in content
    assert "agents-head" in content and "agents-tail" in content
    assert "claude-head" in content and "claude-tail" in content


def test_instruction_truncation_respects_tokens_and_preserves_small_text() -> None:
    assert truncate_text_to_tokens("small", 10) == "small"
    truncated = truncate_text_to_tokens("head-" + "x" * 1000 + "-tail", 40)

    assert estimate_tokens(truncated) <= 40
    assert truncated.startswith("head-")
    assert truncated.endswith("-tail")
    assert TRUNCATION_MARKER in truncated


def test_large_instructions_build_context_for_small_model_and_readme_is_optional(tmp_path: Path) -> None:
    database = _database(tmp_path)
    (tmp_path / "AGENTS.md").write_text("a" * 32_000)
    (tmp_path / "CLAUDE.md").write_text("c" * 32_000)
    (tmp_path / "README.md").write_text("README-OPTIONAL " * 5_000)
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="test", current_model="small", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="small",
        provider_snapshot="test",
        model_snapshot="small",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    store.create_message(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        role="user",
        status="completed",
        content="current request",
    )
    profile = ModelContextProfile("test", "small", 8_192, False)

    context = ContextAssembler(database).build(
        session.session_id,
        turn.turn_id,
        "test",
        "small",
        profile=profile,
    )

    assert sum(estimate_tokens(message) for message in context) + profile.protocol_overhead_tokens <= profile.max_input_tokens
    assert any(message.role == "user" and "current request" in message.content for message in context)
    assert any(message.role == "system" and "PROJECT FILE TRUNCATED" in message.content for message in context)

    tighter = ModelContextProfile("test", "small", 2_048, False)
    tighter_context = ContextAssembler(database).build(
        session.session_id,
        turn.turn_id,
        "test",
        "small",
        profile=tighter,
    )
    assert any(message.role == "user" and "current request" in message.content for message in tighter_context)
    assert all("README-OPTIONAL" not in message.content for message in tighter_context)
