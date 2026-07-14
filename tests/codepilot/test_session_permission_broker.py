from __future__ import annotations

import json
from pathlib import Path

from codepilot.permissions import PermissionRequest, PermissionResponse
from codepilot.session.database import SessionDatabase
from codepilot.session.permission import SessionPermissionBroker
from codepilot.session.store import SessionStore
from codepilot.tui_agent.permission_broker import TestBroker


def test_session_permission_broker_persists_request_response_grant_and_event(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    attempt = store.create_attempt(turn_id=turn.turn_id)
    call = store.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name="replace_range", arguments={"path": "src/demo.py"})
    broker = SessionPermissionBroker(database, session.session_id, TestBroker())
    request = PermissionRequest(
        request_id="perm-1",
        run_id="run-1",
        action_id="act-1",
        tool_name="replace_range",
        arguments_preview={"path": "src/demo.py"},
        reason="need approval",
        risk="local_write",
        side_effect="local_write",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
        session_id=session.session_id,
        turn_id=turn.turn_id,
        attempt_id=attempt.attempt_id,
        tool_call_id=call.tool_call_id,
        scope_key='{"tool":"replace_range","workspace":"/tmp/demo"}',
        scope_json={"tool": "replace_range", "workspace": "/tmp/demo"},
    )

    broker.request(request)
    broker.resolve(
        PermissionResponse(
            request_id="perm-1",
            decision="approve_session",
            reason="approved",
            responded_at="2024-01-01T00:00:01Z",
        )
    )

    with database.transaction() as connection:
        assert connection.execute("SELECT status FROM permission_requests WHERE request_id = ?", ("perm-1",)).fetchone()[0] == "approved"
        response_row = connection.execute("SELECT decision FROM permission_responses WHERE request_id = ?", ("perm-1",)).fetchone()
        assert response_row[0] == "approve_session"
        grant_row = connection.execute("SELECT tool_name, scope_json FROM permission_grants WHERE session_id = ?", (session.session_id,)).fetchone()
        assert grant_row["tool_name"] == "replace_range"
        assert json.loads(grant_row["scope_json"]) == {"tool": "replace_range", "workspace": "/tmp/demo"}
        event_rows = connection.execute("SELECT event_type, payload_json FROM session_events WHERE session_id = ? ORDER BY sequence", (session.session_id,)).fetchall()
        assert [row["event_type"] for row in event_rows] == ["permission_pending", "permission_resolved"]
        assert json.loads(event_rows[-1]["payload_json"])["decision"] == "approve_session"
