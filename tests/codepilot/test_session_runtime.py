from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from codepilot.llm.fake import FakeLLMClient
from codepilot.router import ToolRouter
from codepilot.session.database import SessionDatabase
from codepilot.session.git_context import GitContext
from codepilot.session.models import BranchConfirmationRequired, TurnSubmission
from codepilot.session.runtime import SessionRuntime
from codepilot.session.service import SessionService


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _runtime(tmp_path: Path) -> tuple[SessionRuntime, SessionService, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "demo@example.com")
    _git(repo, "config", "user.name", "Demo")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    service = SessionService(database)
    session = service.create_session(repo, "openai", "gpt-4.1", "manual")
    # 本组测试只验证提交协议，不会进入模型或工具执行链。
    return SessionRuntime(database, object(), lambda trace: object()), service, session.session_id, repo  # type: ignore[arg-type,return-value]


def test_branch_confirmation_atomically_creates_submission(tmp_path: Path) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    _git(repo, "checkout", "-b", "feature")

    pending = runtime.submit_user_message(session_id, "  修复   登录问题  ")

    assert pending == BranchConfirmationRequired(session_id, "main", "feature")
    assert service.store.list_turns(session_id) == []
    assert service.store.list_messages_with_parts(session_id) == []
    assert service.store.list_events(session_id) == []

    submission = runtime.submit_user_message(session_id, "  修复   登录问题  ", confirmed_branch="feature")

    assert isinstance(submission, TurnSubmission)
    assert submission.turn.status == "queued"
    assert submission.turn.branch_snapshot == "feature"
    assert submission.attempt.attempt_number == 1
    assert submission.attempt.status == "created"
    assert service.store.list_messages_with_parts(session_id)[0][0].content == "  修复   登录问题  "
    assert service.store.get_session(session_id).title == "修复 登录问题"
    assert service.store.get_session(session_id).current_branch == "feature"
    assert [event.event_type for event in service.store.list_events(session_id)] == [
        "branch_changed",
        "turn_created",
        "user_message_created",
    ]


def test_confirmation_rechecks_actual_branch_without_writing(tmp_path: Path) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    _git(repo, "checkout", "-b", "feature")
    assert isinstance(runtime.submit_user_message(session_id, "fix"), BranchConfirmationRequired)
    _git(repo, "checkout", "-b", "feature-2")

    pending = runtime.submit_user_message(session_id, "fix", confirmed_branch="feature")

    assert pending == BranchConfirmationRequired(session_id, "feature", "feature-2")
    assert service.store.list_turns(session_id) == []
    assert service.store.list_messages_with_parts(session_id) == []
    assert service.store.list_events(session_id) == []
    assert service.store.get_session(session_id).current_branch == "main"


def test_submission_sql_failure_rolls_back_all_business_facts(tmp_path: Path) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    _git(repo, "checkout", "-b", "feature")
    with runtime.database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_user_message_event BEFORE INSERT ON session_events "
            "WHEN NEW.event_type = 'user_message_created' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )

    with pytest.raises(Exception, match="injected failure"):
        runtime.submit_user_message(session_id, "fix", confirmed_branch="feature")

    assert service.store.list_turns(session_id) == []
    assert service.store.list_messages_with_parts(session_id) == []
    assert service.store.list_events(session_id) == []
    assert service.store.get_session(session_id).title == "New session"
    assert service.store.get_session(session_id).current_branch == "main"


def test_none_branch_can_be_explicitly_confirmed(tmp_path: Path) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    shutil.rmtree(repo / ".git")

    pending = runtime.submit_user_message(session_id, "continue in plain directory")
    submission = runtime.submit_user_message(session_id, "continue in plain directory", confirmed_branch=None)

    assert pending == BranchConfirmationRequired(session_id, "main", None)
    assert isinstance(submission, TurnSubmission)
    assert submission.turn.branch_snapshot is None
    assert service.store.get_session(session_id).current_branch is None


def test_transaction_rechecks_branch_after_initial_validation(tmp_path: Path, monkeypatch) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    _git(repo, "checkout", "-b", "feature")
    monkeypatch.setattr("codepilot.session.runtime.read_git_context", lambda path: GitContext(True, "feature-2"))

    pending = runtime.submit_user_message(session_id, "fix", confirmed_branch="feature")

    assert pending == BranchConfirmationRequired(session_id, "feature", "feature-2")
    assert service.store.list_turns(session_id) == []
    assert service.store.list_events(session_id) == []


class _Cancelled:
    def is_cancelled(self) -> bool:
        return True


class _RaisingLLM:
    def complete(self, messages):  # noqa: ANN001
        raise RuntimeError("provider failed")


def test_run_turn_sets_precise_attempt_times_and_terminal_status(tmp_path: Path) -> None:
    _, service, session_id, repo = _runtime(tmp_path)
    runtime = SessionRuntime(service.database, FakeLLMClient(["hello"]), lambda trace: ToolRouter(trace))
    submission = runtime.submit_user_message(session_id, "say hello")
    assert isinstance(submission, TurnSubmission)

    execution = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)
    attempt = service.store.get_attempt(submission.attempt.attempt_id)

    assert execution.result.status == "message_complete"
    assert execution.result.trace_path is None
    assert attempt.status == "completed"
    assert attempt.started_at is not None
    assert attempt.ended_at is not None
    assert service.store.get_turn(submission.turn.turn_id).status == "completed"
    with pytest.raises(RuntimeError, match="created state"):
        runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)
    assert service.store.get_attempt(submission.attempt.attempt_id).status == "completed"


def test_run_turn_maps_cancelled_and_llm_error_explicitly(tmp_path: Path) -> None:
    _, service, session_id, _ = _runtime(tmp_path)
    cancelled_runtime = SessionRuntime(service.database, FakeLLMClient(["unused"]), lambda trace: ToolRouter(trace))
    cancelled = cancelled_runtime.submit_user_message(session_id, "cancel")
    assert isinstance(cancelled, TurnSubmission)
    result = cancelled_runtime.run_turn(cancelled.turn.turn_id, cancelled.attempt.attempt_id, _Cancelled())
    assert result.result.status == "cancelled"
    assert service.store.get_attempt(cancelled.attempt.attempt_id).status == "cancelled"
    assert service.store.get_turn(cancelled.turn.turn_id).status == "cancelled"

    failed_runtime = SessionRuntime(service.database, _RaisingLLM(), lambda trace: ToolRouter(trace))
    failed = failed_runtime.submit_user_message(session_id, "fail")
    assert isinstance(failed, TurnSubmission)
    result = failed_runtime.run_turn(failed.turn.turn_id, failed.attempt.attempt_id)
    assert result.result.status == "llm_error"
    assert service.store.get_attempt(failed.attempt.attempt_id).status == "failed"
    assert service.store.get_turn(failed.turn.turn_id).status == "failed"


def test_run_turn_setup_exception_is_interrupted(tmp_path: Path) -> None:
    _, service, session_id, _ = _runtime(tmp_path)

    def broken_router(trace):  # noqa: ANN001
        raise RuntimeError("router setup failed")

    runtime = SessionRuntime(service.database, FakeLLMClient(["unused"]), broken_router)
    submission = runtime.submit_user_message(session_id, "fail setup")
    assert isinstance(submission, TurnSubmission)

    with pytest.raises(RuntimeError, match="router setup failed"):
        runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    attempt = service.store.get_attempt(submission.attempt.attempt_id)
    assert attempt.status == "interrupted"
    assert attempt.interruption_reason == "router setup failed"
    assert service.store.get_turn(submission.turn.turn_id).status == "interrupted"
