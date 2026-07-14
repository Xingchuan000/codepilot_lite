from __future__ import annotations

from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolAction, ToolRouter
from codepilot.session.database import SessionDatabase
from codepilot.session.tool_lifecycle import SQLiteToolLifecycleObserver
from codepilot.session.trace_recorder import SessionTraceRecorder
from codepilot.session.store import SessionStore


def _router(tmp_path: Path, *, approved: bool = True) -> tuple[ToolRouter, SessionStore, str]:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
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
    trace = SessionTraceRecorder(database, session.session_id, turn.turn_id, attempt.attempt_id)
    return (
        ToolRouter(
            trace,
            policy_checker=PolicyChecker.default(),
            policy_context=PolicyContext(mode="build", approved=approved, interactive=False),
            lifecycle_observer=SQLiteToolLifecycleObserver(database, session.session_id, turn.turn_id, attempt.attempt_id),
        ),
        store,
        turn.turn_id,
    )


def test_repeated_same_tool_calls_keep_distinct_stable_ids(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    router, store, turn_id = _router(tmp_path)
    action = ToolAction(tool_name="list_files", arguments={"repo": tmp_path, "path": "."})

    first = router.route(action)
    second = router.route(action)
    with store.database.transaction() as connection:
        calls = connection.execute("SELECT tool_call_id, status FROM tool_calls WHERE turn_id = ? ORDER BY created_at, tool_call_id", (turn_id,)).fetchall()
        results = connection.execute("SELECT tool_call_id FROM tool_results ORDER BY tool_call_id").fetchall()

    assert first.metadata["tool_call_id"] != second.metadata["tool_call_id"]
    assert len(calls) == 2
    assert {row["status"] for row in calls} == {"completed"}
    assert {row["tool_call_id"] for row in results} == {row["tool_call_id"] for row in calls}


def test_policy_and_missing_permission_denials_write_terminal_results(tmp_path: Path) -> None:
    router, store, turn_id = _router(tmp_path, approved=False)

    policy_denied = router.route(ToolAction(tool_name="read_file", arguments={"repo": tmp_path, "path": ".env"}))
    permission_denied = router.route(ToolAction(tool_name="run_shell", arguments={"repo": tmp_path, "command": "echo hi"}))
    with store.database.transaction() as connection:
        calls = connection.execute("SELECT status FROM tool_calls WHERE turn_id = ? ORDER BY created_at", (turn_id,)).fetchall()
        results = connection.execute("SELECT status FROM tool_results ORDER BY created_at").fetchall()

    assert policy_denied.success is False
    assert permission_denied.success is False
    assert [row["status"] for row in calls] == ["denied", "denied"]
    assert [row["status"] for row in results] == ["denied", "denied"]


def test_replace_range_persists_recovery_token_before_result(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    router, store, _ = _router(tmp_path)

    routed = router.route(
        ToolAction(
            tool_name="replace_range",
            arguments={"repo": tmp_path, "path": "sample.txt", "start_line": 2, "end_line": 2, "replacement": "changed\n"},
        )
    )
    call = store.get_tool_call(routed.metadata["tool_call_id"])

    assert routed.success is True
    assert call.recovery_token is not None
    assert call.recovery_token["path"] == str(target.resolve())
    assert call.recovery_token["pre_file_sha256"] != call.recovery_token["expected_file_sha256"]
    assert call.side_effect == "local_write"
    assert call.idempotency == "conditional"
    assert call.recovery_strategy == "reconcile_then_retry"


def test_execution_exception_marks_exact_call_uncertain(tmp_path: Path) -> None:
    class BrokenRegistry:
        def has_tool(self, name: str) -> bool:
            raise RuntimeError("registry failed after durable intent")

    router, store, turn_id = _router(tmp_path)
    router.external_tool_registry = BrokenRegistry()

    try:
        router.route(ToolAction(tool_name="list_files", arguments={"repo": tmp_path, "path": "."}))
    except RuntimeError as exc:
        assert str(exc) == "registry failed after durable intent"
    else:
        raise AssertionError("route should preserve the execution exception")

    with store.database.transaction() as connection:
        row = connection.execute("SELECT tool_call_id, status, recovery_token_json FROM tool_calls WHERE turn_id = ?", (turn_id,)).fetchone()
    assert row["status"] == "execution_uncertain"
    assert row["recovery_token_json"] is not None
    assert store.get_tool_result_by_call(row["tool_call_id"]) is None


def test_recovery_token_failure_closes_call_before_side_effect(tmp_path: Path) -> None:
    router, store, turn_id = _router(tmp_path)

    try:
        router.route(
            ToolAction(
                tool_name="replace_range",
                arguments={"repo": tmp_path, "path": "missing.txt", "start_line": 1, "end_line": 1, "replacement": "x"},
            )
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing file must fail before execution")

    with store.database.transaction() as connection:
        row = connection.execute("SELECT tool_call_id, status FROM tool_calls WHERE turn_id = ?", (turn_id,)).fetchone()
    result = store.get_tool_result_by_call(row["tool_call_id"])
    assert row["status"] == "failed"
    assert result is not None
    assert result.metadata == {"executed": False, "phase": "recovery_token"}
