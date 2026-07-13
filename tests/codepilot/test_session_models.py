from __future__ import annotations

from pathlib import Path

from codepilot.session.models import ProjectRecord, SessionRecord, TurnRecord, to_jsonable
from codepilot.session.paths import resolve_session_paths


def test_session_records_can_be_converted_to_jsonable() -> None:
    record = SessionRecord(
        session_id="sess-1",
        project_id="proj-1",
        title="New session",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
        initial_branch=None,
        current_branch="main",
        status="active",
        parent_session_id=None,
        forked_from_turn_id=None,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        last_activity_at="2024-01-01T00:00:00+00:00",
        metadata={"path": Path("/tmp/repo")},
    )

    assert to_jsonable(record)["metadata"]["path"] == "/tmp/repo"


def test_project_and_turn_records_keep_expected_fields() -> None:
    assert ProjectRecord(
        project_id="proj-1",
        path=Path("/tmp/repo"),
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    ).path == Path("/tmp/repo")
    assert TurnRecord(
        turn_id="turn-1",
        session_id="sess-1",
        sequence=1,
        title="Turn 1",
        status="queued",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        last_activity_at="2024-01-01T00:00:00+00:00",
    ).sequence == 1


def test_resolve_session_paths_uses_explicit_base_dir(tmp_path: Path) -> None:
    paths = resolve_session_paths(tmp_path)

    assert paths.data_dir == tmp_path
    assert paths.database_path == tmp_path / "sessions.sqlite3"
    assert paths.sessions_dir == tmp_path / "sessions"
    assert paths.exports_dir == tmp_path / "exports"
