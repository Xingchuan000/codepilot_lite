from __future__ import annotations

from pathlib import Path

from codepilot.multi_agent.supervisor import AgentSupervisor
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def _completed_child(tmp_path: Path, *, runtime_result: str | None = "runtime summary fallback"):
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionStore(database)
    repo = tmp_path / "repo"
    repo.mkdir()

    parent = store.create_session(
        project_path=repo,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
        initial_branch="main",
        current_branch="main",
    )
    parent_turn = store.create_turn(
        session_id=parent.session_id,
        title="parent",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
        status="completed",
    )
    child = store.create_session(
        project_path=repo,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
        initial_branch="main",
        current_branch="main",
        parent_session_id=parent.session_id,
        forked_from_turn_id=parent_turn.turn_id,
        metadata={
            "agent_type": "explore",
            "agent_status": "completed",
            **({"result": runtime_result} if runtime_result is not None else {}),
            "write_scope": [],
        },
    )
    child_turn = store.create_turn(
        session_id=child.session_id,
        title="child",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
        status="completed",
    )
    supervisor = AgentSupervisor(
        database=database,
        child_runtime_factory=lambda _child_id: None,
    )
    return supervisor, store, ArtifactStore(database), parent, child, child_turn


def test_wait_reconstructs_streamed_assistant_result_when_runtime_summary_missing(tmp_path: Path) -> None:
    supervisor, store, _artifacts, parent, child, child_turn = _completed_child(tmp_path, runtime_result=None)
    message = store.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        attempt_id=None,
        role="assistant",
        status="completed",
        content="",
    )
    store.append_message_part(message.message_id, type="reasoning", content="private reasoning")
    store.append_message_part(message.message_id, type="text", content="Root cause: ")
    store.append_message_part(message.message_id, type="text", content="VIP uses 5% instead of 10%.")

    result = supervisor.wait(parent.session_id, child.session_id, timeout=0)

    assert result["result"] == "Root cause: VIP uses 5% instead of 10%."


def test_snapshot_reads_full_text_from_assistant_artifact_part_when_runtime_summary_missing(tmp_path: Path) -> None:
    supervisor, store, artifacts, _parent, child, child_turn = _completed_child(tmp_path, runtime_result=None)
    full_result = "evidence:" + ("x" * 17_000)
    persisted = artifacts.persist_content(child.session_id, "assistant_message_delta", full_result)
    assert persisted.artifact_id is not None
    message = store.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        attempt_id=None,
        role="assistant",
        status="completed",
        content="",
    )
    store.append_message_part(
        message.message_id,
        type="text",
        content=persisted.preview,
        artifact_id=persisted.artifact_id,
    )

    snapshot = supervisor.snapshot(child.session_id)

    assert snapshot["result"] == full_result


def test_list_agents_falls_back_to_runtime_summary_when_completed_assistant_has_no_text(tmp_path: Path) -> None:
    supervisor, store, _artifacts, parent, child, child_turn = _completed_child(tmp_path)
    message = store.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        attempt_id=None,
        role="assistant",
        status="completed",
        content="",
    )
    store.append_message_part(
        message.message_id,
        type="tool_call",
        content={"tool_name": "read_file", "arguments": {"path": "README.md"}},
    )

    listed = supervisor.list_agents(parent.session_id)

    assert len(listed) == 1
    assert listed[0]["result"] == "runtime summary fallback"


def test_snapshot_prefers_structured_runtime_summary_and_exposes_child_evidence(tmp_path: Path) -> None:
    supervisor, store, _artifacts, parent, child, child_turn = _completed_child(
        tmp_path,
        runtime_result="Fixed pricing bug; tests passed; diff checked.",
    )
    store.update_session(
        child.session_id,
        metadata={
            **store.get_session(child.session_id).metadata,
            "tests": "python -m pytest -q tests/test_pricing.py -> 3 passed",
            "test_status": "passed",
            "diff_checked": True,
            "missing_evidence": [],
            "completion_kind": "task_success",
            "delivery_kind": "code_change",
            "patch_artifact_id": None,
        },
    )
    message = store.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        attempt_id=None,
        role="assistant",
        status="completed",
        content="",
    )
    store.append_message_part(message.message_id, type="text", content="Task complete.")

    snapshot = supervisor.wait(parent.session_id, child.session_id, timeout=0)

    assert snapshot["result"] == "Fixed pricing bug; tests passed; diff checked."
    assert snapshot["tests"] == "python -m pytest -q tests/test_pricing.py -> 3 passed"
    assert snapshot["test_status"] == "passed"
    assert snapshot["diff_checked"] is True
    assert snapshot["missing_evidence"] == []
    assert snapshot["completion_kind"] == "task_success"
    assert snapshot["delivery_kind"] == "code_change"
