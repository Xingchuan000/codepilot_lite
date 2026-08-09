from __future__ import annotations

from pathlib import Path

from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore


def test_restart_without_child_handle_marks_running_child_recovery_required(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    service = SessionService(database)
    store = SessionStore(database)
    project = tmp_path / "repo"
    project.mkdir()
    parent = service.create_session(project, "fake", "fake", "manual")
    parent_turn = store.create_turn(
        session_id=parent.session_id,
        title="primary",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    child = service.create_child_session(
        parent_session_id=parent.session_id,
        forked_from_turn_id=parent_turn.turn_id,
        provider="fake",
        model="fake",
        permission_mode="manual",
        metadata={"agent_type": "explore", "agent_status": "running", "memory_write": False},
    )
    child_turn = store.create_turn(
        session_id=child.session_id,
        title="child",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="running",
    )

    restarted = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)

    snapshot = restarted.snapshot(child.session_id)

    assert snapshot["status"] == "recovery_required"
    assert store.get_session(child.session_id).metadata["agent_status"] == "recovery_required"
    assert restarted.list_agents(parent.session_id) == [snapshot]
    assert store.get_turn(child_turn.turn_id).status == "running"
