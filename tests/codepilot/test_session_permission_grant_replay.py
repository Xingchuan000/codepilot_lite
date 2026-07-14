from __future__ import annotations

from pathlib import Path

from codepilot.permissions import PermissionRequest, PermissionResponse
from codepilot.session.database import SessionDatabase
from codepilot.session.permission import SessionPermissionBroker
from codepilot.session.store import SessionStore
from codepilot.tui_agent.permission_broker import TestBroker


def test_persisted_grant_is_replayed_without_ui_request(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    request = PermissionRequest(
        request_id="permission-first",
        run_id="run-1",
        action_id="action-1",
        tool_name="run_shell",
        arguments_preview={"command": "git status"},
        reason="run command",
        risk="shell_execution",
        side_effect="read_only",
        matched_rule="ask",
        created_at="2024-01-01T00:00:00Z",
        session_id=session.session_id,
        scope_key='{"tool":"run_shell","command_hash":"same"}',
        scope_json={"tool": "run_shell", "command_hash": "same"},
    )
    first_inner = TestBroker()
    first = SessionPermissionBroker(database, session.session_id, first_inner)
    first.request(request)
    first.resolve(PermissionResponse(request.request_id, "approve_session", "approved", "2024-01-01T00:00:01Z"))

    second_inner = TestBroker()
    second = SessionPermissionBroker(database, session.session_id, second_inner)
    replayed = PermissionRequest(**{**request.__dict__, "request_id": "permission-second"})
    second.request(replayed)

    assert second_inner.requests == []
    assert second.wait(replayed.request_id).decision == "approve_session"
