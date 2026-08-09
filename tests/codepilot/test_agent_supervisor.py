from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.multi_agent.models import SpawnContract
from codepilot.multi_agent.supervisor import AgentSupervisor, AgentSupervisorConfig, scopes_may_overlap
from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    return repo


class _ChildRuntime:
    def __init__(self, store: SessionStore, child_id: str, mode: str) -> None:
        self.store = store
        self.child_id = child_id
        self.mode = mode

    def submit_user_message(self, session_id: str, text: str, **kwargs):
        return SimpleNamespace(
            turn=SimpleNamespace(turn_id=f"turn-{self.child_id}"),
            attempt=SimpleNamespace(attempt_id=f"attempt-{self.child_id}"),
        )

    def run_turn(self, turn_id: str, attempt_id: str, cancellation_token):
        session = self.store.get_session(self.child_id)
        workspace = session.metadata.get("workspace_path")
        if self.mode in {"write", "write_outside"} and isinstance(workspace, str):
            Path(workspace, "README.md").write_text("after\n", encoding="utf-8")
        if self.mode == "write_outside" and isinstance(workspace, str):
            Path(workspace, "OUTSIDE.md").write_text("unexpected\n", encoding="utf-8")
        while self.mode == "blocking" and not cancellation_token.is_cancelled():
            time.sleep(0.01)
        status = "cancelled" if cancellation_token.is_cancelled() else "success"
        return SimpleNamespace(result=SimpleNamespace(status=status, summary="child result", error=None))


def _fixture(tmp_path: Path, mode: str = "complete", config: AgentSupervisorConfig | None = None):
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    service = SessionService(database)
    store = SessionStore(database)
    repo = _git_repo(tmp_path)
    parent = service.create_session(repo, "openai", "fake", "manual")
    turn = store.create_turn(
        session_id=parent.session_id,
        title="primary turn",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
    )
    events: list[dict[str, object]] = []
    supervisor = AgentSupervisor(
        database=database,
        child_runtime_factory=lambda child_id: _ChildRuntime(store, child_id, mode),
        event_sink=events.append,
        config=config,
    )
    context = SimpleNamespace(
        parent_session_id=parent.session_id,
        parent_turn_id=turn.turn_id,
        parent_attempt_id="attempt-primary",
        parent_repo=repo,
    )
    return supervisor, context, store, parent, repo, events


def test_spawn_explore_persists_child_and_wait_reads_sqlite_tree(tmp_path: Path) -> None:
    supervisor, context, store, parent, repo, events = _fixture(tmp_path)

    spawned = supervisor.spawn(context=context, contract=SpawnContract(agent_type="explore", task="inspect"))
    result = supervisor.wait(parent.session_id, str(spawned["agent_id"]), timeout=5)

    assert result["status"] == "completed"
    child = store.get_session(str(spawned["agent_id"]))
    assert child.parent_session_id == parent.session_id
    assert child.project_id == parent.project_id
    assert [item["agent_id"] for item in supervisor.list_agents(parent.session_id)] == [child.session_id]
    assert {item["type"] for item in events} >= {"agent_spawned", "agent_started", "agent_completed"}
    assert {item.event_type for item in store.list_events(parent.session_id)} >= {
        "agent_spawned",
        "agent_started",
        "agent_completed",
    }


def test_general_writer_uses_worktree_and_explicit_apply_only(tmp_path: Path) -> None:
    supervisor, context, store, parent, repo, events = _fixture(tmp_path, mode="write")
    spawned = supervisor.spawn(
        context=context,
        contract=SpawnContract(agent_type="general", task="update readme", write_scope=("README.md",)),
    )

    result = supervisor.wait(parent.session_id, str(spawned["agent_id"]), timeout=5)
    child = store.get_session(str(spawned["agent_id"]))
    assert result["status"] == "completed"
    assert Path(str(child.metadata["workspace_path"]), "README.md").read_text(encoding="utf-8") == "after\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "before\n"
    patch = supervisor.inspect_agent_patch(parent.session_id, child.session_id)
    assert patch["changed_files"] == ["README.md"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "before\n"

    applied = supervisor.apply_agent_patch(parent.session_id, child.session_id, repo)

    assert applied.success is True
    assert (repo / "README.md").read_text(encoding="utf-8") == "after\n"
    assert not (repo / ".git" / "index.lock").exists()
    assert any(item["type"] == "agent_patch_applied" for item in events)



def test_general_writer_scope_violation_fails_before_patch_ready(tmp_path: Path) -> None:
    supervisor, context, store, parent, repo, events = _fixture(tmp_path, mode="write_outside")
    spawned = supervisor.spawn(
        context=context,
        contract=SpawnContract(agent_type="general", task="update readme", write_scope=("README.md",)),
    )

    result = supervisor.wait(parent.session_id, str(spawned["agent_id"]), timeout=5)
    child = store.get_session(str(spawned["agent_id"]))

    assert result["status"] == "failed"
    assert child.metadata["scope_violation_files"] == ["OUTSIDE.md"]
    assert "outside write_scope" in str(result["error"])
    assert result["patch_artifact_id"] is not None
    assert not any(item["type"] == "agent_patch_ready" for item in events)
    assert any(item["type"] == "agent_failed" for item in events)
    assert (repo / "README.md").read_text(encoding="utf-8") == "before\n"
    assert not (repo / "OUTSIDE.md").exists()

def test_general_patch_cannot_be_applied_before_primary_inspects_it(tmp_path: Path) -> None:
    supervisor, context, store, parent, repo, events = _fixture(tmp_path, mode="write")
    spawned = supervisor.spawn(
        context=context,
        contract=SpawnContract(agent_type="general", task="update readme", write_scope=("README.md",)),
    )
    supervisor.wait(parent.session_id, str(spawned["agent_id"]), timeout=5)

    result = supervisor.apply_agent_patch(parent.session_id, str(spawned["agent_id"]), repo)

    assert result.success is False
    assert "inspect" in (result.error or "")


def test_writer_scope_overlap_and_parent_read_only_are_rejected(tmp_path: Path) -> None:
    supervisor, context, store, parent, repo, events = _fixture(tmp_path, mode="blocking")
    first = supervisor.spawn(
        context=context,
        contract=SpawnContract(agent_type="general", task="one", write_scope=("src/**",)),
    )
    with pytest.raises(RuntimeError, match="overlaps"):
        supervisor.spawn(
            context=context,
            contract=SpawnContract(agent_type="general", task="two", write_scope=("src/main.py",)),
        )
    supervisor.close(parent.session_id, str(first["agent_id"]))

    readonly = store.update_session(parent.session_id, permission_mode="read_only")
    assert readonly.permission_mode == "read_only"
    with pytest.raises(PermissionError, match="read-only"):
        supervisor.spawn(
            context=context,
            contract=SpawnContract(agent_type="general", task="write", write_scope=("README.md",)),
        )


def test_max_children_depth_and_wait_timeout_are_enforced(tmp_path: Path) -> None:
    config = AgentSupervisorConfig(max_active_children=1, wait_timeout_seconds=0.05)
    supervisor, context, store, parent, repo, events = _fixture(tmp_path, mode="blocking", config=config)
    first = supervisor.spawn(context=context, contract=SpawnContract(agent_type="scout", task="research"))
    with pytest.raises(RuntimeError, match="maximum active child"):
        supervisor.spawn(context=context, contract=SpawnContract(agent_type="explore", task="second"))
    assert supervisor.wait(parent.session_id, str(first["agent_id"]), timeout=0)["status"] == "running"
    child_turn = store.create_turn(
        session_id=str(first["agent_id"]),
        title="child",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
    )
    with pytest.raises(PermissionError, match="Primary"):
        supervisor.spawn(
            context=SimpleNamespace(
                parent_session_id=str(first["agent_id"]),
                parent_turn_id=child_turn.turn_id,
                parent_attempt_id="attempt-child",
                parent_repo=repo,
            ),
            contract=SpawnContract(agent_type="explore", task="nested"),
        )
    supervisor.close(parent.session_id, str(first["agent_id"]))
    assert supervisor.snapshot(str(first["agent_id"]))["status"] == "cancelled"


def test_scope_overlap_is_conservative_for_wildcards() -> None:
    assert scopes_may_overlap(("src/**",), ("tests/test.py",)) is True
    assert scopes_may_overlap(("src/a.py",), ("tests/test.py",)) is False
