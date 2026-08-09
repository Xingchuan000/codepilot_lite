from __future__ import annotations

import json
from pathlib import Path

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.exporter import SessionExporter
from codepilot.session.store import SessionStore


def test_export_writes_trace_recursive_manifest_and_preserves_activity(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    store.append_event(session_id=session.session_id, event_type="turn_created", payload={"turn_id": "none"})
    artifact = ArtifactStore(database).put_text(session.session_id, "tool_result", "x" * 20_000)
    before = store.get_session(session.session_id).last_activity_at

    exported = SessionExporter(database).export(session.session_id, tmp_path / "exports")
    manifest = json.loads((exported / "manifest.json").read_text(encoding="utf-8"))
    paths = {item["relative_path"] for item in manifest["files"]}

    assert "trace.jsonl" in paths
    assert f"artifacts/{artifact.artifact_id}.txt" in paths
    assert store.get_session(session.session_id).last_activity_at == before


def test_export_parent_recursively_includes_child_agent_sessions_and_traces(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    parent = store.create_session(
        project_path=tmp_path,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
    )
    child = store.create_session(
        project_path=tmp_path,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
        parent_session_id=parent.session_id,
        metadata={"agent_type": "explore"},
    )
    grandchild = store.create_session(
        project_path=tmp_path,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
        parent_session_id=child.session_id,
        metadata={"agent_type": "general"},
    )
    child_turn = store.create_turn(
        session_id=child.session_id,
        title="explore VIP discount",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="completed",
    )
    child_message = store.create_message(
        session_id=child.session_id,
        turn_id=child_turn.turn_id,
        role="assistant",
        status="completed",
        content="",
    )
    store.append_message_part(
        child_message.message_id,
        type="text",
        content="VIP discount root cause",
    )
    store.append_event(
        session_id=child.session_id,
        event_type="child_trace_marker",
        payload={"agent_type": "explore"},
        turn_id=child_turn.turn_id,
    )
    store.append_event(
        session_id=grandchild.session_id,
        event_type="grandchild_trace_marker",
        payload={"agent_type": "general"},
    )
    child_artifact = ArtifactStore(database).put_text(
        child.session_id, "tool_result", "child artifact"
    )

    exported = SessionExporter(database).export(parent.session_id, tmp_path / "exports")
    child_dir = exported / "child_sessions" / child.session_id
    grandchild_dir = child_dir / "child_sessions" / grandchild.session_id

    assert child_dir.is_dir()
    assert grandchild_dir.is_dir()
    child_session = json.loads((child_dir / "session.json").read_text(encoding="utf-8"))
    grandchild_session = json.loads(
        (grandchild_dir / "session.json").read_text(encoding="utf-8")
    )
    assert child_session["session"]["session_id"] == child.session_id
    assert child_session["session"]["parent_session_id"] == parent.session_id
    assert grandchild_session["session"]["session_id"] == grandchild.session_id
    assert grandchild_session["session"]["parent_session_id"] == child.session_id

    child_turn_records = [
        json.loads(line)
        for line in (child_dir / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    child_message_records = [
        json.loads(line)
        for line in (child_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row["record_type"] == "turn" and row["turn_id"] == child_turn.turn_id
        for row in child_turn_records
    )
    assert any(
        row["record_type"] == "message"
        and row["message_id"] == child_message.message_id
        for row in child_message_records
    )
    assert any(
        row["record_type"] == "message_part"
        and row["message_id"] == child_message.message_id
        and json.loads(row["content_json"]) == "VIP discount root cause"
        for row in child_message_records
    )

    child_trace = [
        json.loads(line)
        for line in (child_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    grandchild_trace = [
        json.loads(line)
        for line in (grandchild_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row["event_type"] == "child_trace_marker"
        and row["session_id"] == child.session_id
        for row in child_trace
    )
    assert any(
        row["event_type"] == "grandchild_trace_marker"
        and row["session_id"] == grandchild.session_id
        for row in grandchild_trace
    )
    assert (child_dir / "artifacts" / f"{child_artifact.artifact_id}.txt").read_text(
        encoding="utf-8"
    ) == "child artifact"

    root_report = json.loads((exported / "report.json").read_text(encoding="utf-8"))
    assert root_report["child_session_ids"] == [child.session_id]
    assert root_report["descendant_session_ids"] == [child.session_id, grandchild.session_id]
    assert root_report["descendant_session_count"] == 2

    root_manifest = json.loads((exported / "manifest.json").read_text(encoding="utf-8"))
    root_paths = {item["relative_path"] for item in root_manifest["files"]}
    assert f"child_sessions/{child.session_id}/trace.jsonl" in root_paths
    assert f"child_sessions/{child.session_id}/manifest.json" in root_paths
    assert (
        f"child_sessions/{child.session_id}/child_sessions/{grandchild.session_id}/trace.jsonl"
        in root_paths
    )
    assert (
        f"child_sessions/{child.session_id}/artifacts/{child_artifact.artifact_id}.txt"
        in root_paths
    )
    assert root_manifest["child_session_ids"] == [child.session_id]
    assert root_manifest["descendant_session_count"] == 2


def test_export_leaf_session_keeps_existing_root_layout_without_child_directory(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(
        project_path=tmp_path,
        provider="openai",
        current_model="fake",
        permission_mode="manual",
    )

    exported = SessionExporter(database).export(session.session_id, tmp_path / "exports")

    assert (exported / "session.json").is_file()
    assert (exported / "trace.jsonl").is_file()
    assert not (exported / "child_sessions").exists()
    manifest = json.loads((exported / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((exported / "report.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "codepilot.session.export.v2"
    assert manifest["child_session_ids"] == []
    assert manifest["descendant_session_count"] == 0
    assert report["descendant_session_ids"] == []
