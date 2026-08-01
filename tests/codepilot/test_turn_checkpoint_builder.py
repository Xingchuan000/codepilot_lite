from pathlib import Path

from codepilot.memory.turn_checkpoint_builder import TurnCheckpointBuilder
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore


def test_builder_uses_persisted_tool_facts_for_files_tests_errors_and_next_step(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="tiny", permission_mode="manual")
    turn = store.create_turn(session_id=session.session_id, title="facts", provider_snapshot="openai", model_snapshot="tiny", permission_mode_snapshot="manual", branch_snapshot=None)
    read = store.create_tool_call(turn_id=turn.turn_id, tool_name="read_file", arguments={"path": "src/app.py"})
    store.persist_tool_result(read.tool_call_id, call_status="completed", result_status="success", content="source", success=True, metadata={"path": "src/app.py"})
    tests = store.create_tool_call(turn_id=turn.turn_id, tool_name="run_tests", arguments={"command": "pytest -q"})
    store.persist_tool_result(tests.tool_call_id, call_status="failed", result_status="failed", content="failed", error="AssertionError", success=False, metadata={"command": "pytest -q", "status": "failed", "returncode": 1, "summary_line": "1 failed"})

    content = TurnCheckpointBuilder(database).build(
        session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix tests",
        evidence={"missing": ["missing_passed_tests"]}, covered_message_ids=(), previous=None,
    )

    assert content.current_goal == "fix tests"
    assert content.files_read == ("src/app.py",)
    assert content.commands_run == ("pytest -q",)
    assert "1 failed" in content.test_results[0]
    assert content.current_errors == ("[pytest:*] AssertionError",)
    assert content.next_step == "Run the relevant tests and obtain a passing result."


def test_later_passing_test_clears_active_failure_but_keeps_history(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    failed = store.create_tool_call(turn_id=turn.turn_id, tool_name="run_tests", arguments={"command": "pytest -q"})
    store.persist_tool_result(failed.tool_call_id, call_status="failed", result_status="failed", content="failed", error="AssertionError", success=False, metadata={"command": "pytest -q", "status": "failed", "summary_line": "1 failed"})
    passed = store.create_tool_call(turn_id=turn.turn_id, tool_name="run_tests", arguments={"command": "pytest -q"})
    store.persist_tool_result(passed.tool_call_id, call_status="completed", result_status="success", content="passed", success=True, metadata={"command": "pytest -q", "status": "passed", "summary_line": "10 passed"})

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=4, task="fix", evidence={"last_test_status": "passed"}, covered_message_ids=(), previous=None)

    assert content.current_errors == ()
    assert content.next_step == ""
    assert any("failed" in item for item in content.test_results)
    assert any("passed" in item for item in content.test_results)


def test_pending_large_patch_is_hashed_without_changing_sqlite_fact(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    patch = "x" * 20_000
    call = store.create_tool_call(turn_id=turn.turn_id, tool_name="apply_patch", arguments={"path": "src/app.py", "patch": patch})

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=2, task="apply", evidence={"unknown": "y" * 20_000}, covered_message_ids=(), previous=None)

    arguments = content.pending_tool_calls[0]["arguments"]
    assert arguments["patch_chars"] == 20_000
    assert len(arguments["patch_sha256"]) == 64
    assert patch not in str(content.to_dict())
    assert content.evidence == {}
    assert store.get_tool_call(call.tool_call_id).arguments["patch"] == patch


def test_recent_tool_facts_only_include_covered_tool_exchange(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    covered_assistant = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content="first")
    covered_call = store.create_tool_call(turn_id=turn.turn_id, tool_name="read_file", arguments={"path": "old.py"}, message_id=covered_assistant.message_id)
    store.persist_tool_result(covered_call.tool_call_id, call_status="completed", result_status="success", content="old", success=True, metadata={"path": "old.py"})
    covered_result = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content="old", metadata={"tool_call_id": covered_call.tool_call_id, "success": True})
    retained_assistant = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content="latest")
    retained_call = store.create_tool_call(turn_id=turn.turn_id, tool_name="read_file", arguments={"path": "new.py"}, message_id=retained_assistant.message_id)
    store.persist_tool_result(retained_call.tool_call_id, call_status="completed", result_status="success", content="new", success=True, metadata={"path": "new.py"})

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="inspect", evidence={}, covered_message_ids=(covered_assistant.message_id, covered_result.message_id), previous=None)

    assert [fact["tool_call_id"] for fact in content.recent_tool_facts] == [covered_call.tool_call_id]
    assert retained_call.tool_call_id not in str(content.recent_tool_facts)


def test_unconsumed_parse_error_is_current_until_later_assistant_progress(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="Action parse failed. bad JSON", metadata={"synthetic": True, "category": "parse_error"})

    active = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=2, task="inspect", evidence={}, covered_message_ids=(), previous=None)
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content='{"type":"finish"}')
    resolved = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="inspect", evidence={}, covered_message_ids=(), previous=None)

    assert "Action parse failed" in active.current_errors[0]
    assert resolved.current_errors == ()


def test_evidence_block_remains_active_until_missing_evidence_is_cleared(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="Finish blocked. missing tests", metadata={"synthetic": True, "category": "evidence_blocked"})

    active = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=2, task="fix", evidence={"missing_evidence": ["missing_passed_tests"]}, covered_message_ids=(), previous=None)
    resolved = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={"missing_evidence": []}, covered_message_ids=(), previous=None)

    assert "Finish blocked" in active.current_errors[0]
    assert resolved.current_errors == ()


def test_full_tests_directory_pass_clears_specific_pytest_failure(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "python -m pytest tests/foo.py", False, "1 failed")
    _persist_test(store, turn.turn_id, "python -m pytest tests/", True, "100 passed")

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None)

    assert content.current_errors == ()
    assert len(content.test_results) == 2


def test_pytest_subdirectory_pass_does_not_clear_other_test_scope(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "pytest tests/integration/", False, "integration failed")
    _persist_test(store, turn.turn_id, "pytest tests/unit/", True, "unit passed")

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None)

    assert content.current_errors == ("[pytest:tests/integration/] integration failed",)


def test_project_root_pytest_pass_clears_specific_failure(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "pytest tests/foo.py", False, "1 failed")
    _persist_test(store, turn.turn_id, "pytest .", True, "100 passed")

    assert TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None).current_errors == ()


def test_same_pytest_scope_with_different_flags_clears_failure(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "pytest tests/foo.py", False, "1 failed")
    _persist_test(store, turn.turn_id, "pytest -q tests/foo.py", True, "1 passed")

    assert TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None).current_errors == ()


def test_different_subset_pass_does_not_clear_other_failure(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "pytest tests/a.py", False, "a failed")
    _persist_test(store, turn.turn_id, "pytest tests/b.py", True, "b passed")

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None)

    assert content.current_errors == ("[pytest:tests/a.py] a failed",)
    assert content.next_step


def test_pytest_k_filter_is_not_treated_as_full_suite(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "pytest tests/a.py", False, "a failed")
    _persist_test(store, turn.turn_id, "pytest -k memory", True, "memory passed")

    assert TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None).current_errors


def test_unknown_test_framework_only_clears_exact_command(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "mypy src", False, "mypy failed")
    _persist_test(store, turn.turn_id, "mypy src --strict", True, "mypy passed")

    assert TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None).current_errors == ("[command:mypy src] mypy failed",)


def test_non_executing_pytest_does_not_clear_test_failure(tmp_path: Path) -> None:
    database, store, session, turn = _facts(tmp_path)
    _persist_test(store, turn.turn_id, "pytest tests/a.py", False, "a failed")
    _persist_test(store, turn.turn_id, "pytest --collect-only tests/", True, "collected 1 item")

    content = TurnCheckpointBuilder(database).build(session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=3, task="fix", evidence={}, covered_message_ids=(), previous=None)

    assert content.current_errors == ("[pytest:tests/a.py] a failed",)


def _facts(tmp_path: Path):
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="tiny", permission_mode="manual")
    turn = store.create_turn(session_id=session.session_id, title="facts", provider_snapshot="openai", model_snapshot="tiny", permission_mode_snapshot="manual", branch_snapshot=None)
    return database, store, session, turn


def _persist_test(store: SessionStore, turn_id: str, command: str, success: bool, summary: str) -> None:
    call = store.create_tool_call(turn_id=turn_id, tool_name="run_tests", arguments={"command": command})
    store.persist_tool_result(
        call.tool_call_id,
        call_status="completed" if success else "failed",
        result_status="success" if success else "failed",
        content=summary,
        error=None if success else summary,
        success=success,
        metadata={"command": command, "status": "passed" if success else "failed", "summary_line": summary},
    )
