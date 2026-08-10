from __future__ import annotations

import threading
import time
from pathlib import Path

from codepilot.multi_agent.models import AgentHandle
from codepilot.multi_agent.supervisor import AgentSupervisor, _RunningChild
from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore


def _waiting_child(tmp_path: Path):
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    service = SessionService(database)
    store = SessionStore(database)
    repo = tmp_path / "repo"
    repo.mkdir()
    parent = service.create_session(repo, "fake", "fake", "manual")
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
        metadata={"agent_type": "general", "agent_status": "running", "write_scope": ["README.md"]},
    )
    store.create_turn(
        session_id=child.session_id,
        title="child",
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="waiting_permission",
    )
    supervisor = AgentSupervisor(database=database, child_runtime_factory=lambda _: None)
    return supervisor, parent, child, parent_turn


def test_snapshot_exposes_live_child_waiting_permission(tmp_path: Path) -> None:
    supervisor, parent, child, parent_turn = _waiting_child(tmp_path)
    blocker = threading.Event()
    thread = threading.Thread(target=blocker.wait, daemon=True)
    thread.start()
    supervisor._running[child.session_id] = _RunningChild(
        AgentHandle(
            child_session_id=child.session_id,
            parent_session_id=parent.session_id,
            parent_turn_id=parent_turn.turn_id,
            agent_type="general",
            write_scope=("README.md",),
        ),
        thread,
        threading.Event(),
    )
    try:
        assert supervisor.snapshot(child.session_id)["status"] == "waiting_permission"
    finally:
        blocker.set()
        thread.join(1)


def test_wait_timeout_pauses_while_child_waits_for_human_permission(tmp_path: Path) -> None:
    supervisor, parent, child, parent_turn = _waiting_child(tmp_path)
    release = threading.Event()

    def child_worker() -> None:
        release.wait()
        supervisor._set_status(child.session_id, "completed", result="done")

    thread = threading.Thread(target=child_worker, daemon=True)
    supervisor._running[child.session_id] = _RunningChild(
        AgentHandle(
            child_session_id=child.session_id,
            parent_session_id=parent.session_id,
            parent_turn_id=parent_turn.turn_id,
            agent_type="general",
            write_scope=("README.md",),
        ),
        thread,
        threading.Event(),
    )
    thread.start()
    timer = threading.Timer(0.15, release.set)
    timer.start()
    started = time.monotonic()
    try:
        snapshot = supervisor.wait(parent.session_id, child.session_id, timeout=0.05)
    finally:
        release.set()
        timer.cancel()
        thread.join(1)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.10
    assert snapshot["status"] == "completed"
