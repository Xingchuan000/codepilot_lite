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
