from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore


def _service(tmp_path: Path) -> tuple[SessionService, SessionStore]:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    return SessionService(database), SessionStore(database)


def test_child_session_reuses_project_and_overrides_only_effective_workspace(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    source = (tmp_path / "repo").resolve()
    workspace = (tmp_path / "worktree").resolve()
    source.mkdir()
    workspace.mkdir()
    parent = service.create_session(source, "openai", "fake", "manual")
    fork = store.create_turn(
        session_id=parent.session_id,
        title="parent turn",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )

    child = service.create_child_session(
        parent_session_id=parent.session_id,
        forked_from_turn_id=fork.turn_id,
        provider="openai",
        model="fake",
        permission_mode="manual",
        metadata={"agent_type": "general", "workspace_path": str(workspace)},
    )

    assert child.project_id == parent.project_id
    assert service.open_session(parent.session_id).project_path == source
    assert service.open_session(child.session_id).project_path == workspace
    assert store.list_child_sessions(parent.session_id) == [child]


def test_child_creation_rejects_fork_turn_from_another_parent(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    first = service.create_session(tmp_path / "one", "openai", "fake", "manual")
    second = service.create_session(tmp_path / "two", "openai", "fake", "manual")
    first_turn = store.create_turn(
        session_id=first.session_id,
        title="first",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )

    with pytest.raises(ValueError, match="fork turn does not belong"):
        service.create_child_session(
            parent_session_id=second.session_id,
            forked_from_turn_id=first_turn.turn_id,
            provider="openai",
            model="fake",
            permission_mode="manual",
            metadata={},
        )
