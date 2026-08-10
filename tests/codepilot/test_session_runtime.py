from __future__ import annotations

import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.llm.fake import StructuredFakeLLM
from codepilot.llm.types import LLMResponse
from codepilot.router import ToolRouter
from codepilot.session.database import SessionDatabase
from codepilot.session.git_context import GitContext
from codepilot.session.models import BranchConfirmationRequired, TurnSubmission
from codepilot.session.runtime import SessionRuntime, _renew_lease_until_stopped
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


class _HeartbeatStop:
    def __init__(self, waits: list[bool]) -> None:
        self.waits = iter(waits)

    def wait(self, timeout: float) -> bool:
        return next(self.waits, True)

    def is_set(self) -> bool:
        return False


class _RenewingStore:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = iter(failures)
        self.calls = 0
        self.attempts = SimpleNamespace(renew_attempt_lease=self.renew_attempt_lease)

    def renew_attempt_lease(self, attempt_id: str, worker_id: str, lease_expires_at: str) -> None:
        self.calls += 1
        failure = next(self.failures, None)
        if failure is not None:
            raise failure


def test_heartbeat_retries_transient_sqlite_busy_then_succeeds() -> None:
    store = _RenewingStore([sqlite3.OperationalError("database is locked")] * 2)
    lease_lost = threading.Event()

    _renew_lease_until_stopped(store, "attempt", "worker", _HeartbeatStop([False, False, False, True]), lease_lost)

    assert store.calls == 3
    assert not lease_lost.is_set()


def test_heartbeat_stops_after_maximum_sqlite_busy_retries() -> None:
    store = _RenewingStore([sqlite3.OperationalError("database is busy")] * 7)
    lease_lost = threading.Event()

    _renew_lease_until_stopped(store, "attempt", "worker", _HeartbeatStop([False] * 6), lease_lost)

    assert store.calls == 6
    assert lease_lost.is_set()


def test_heartbeat_stops_on_unexpected_error() -> None:
    store = _RenewingStore([ValueError("bad lease state")])
    lease_lost = threading.Event()

    _renew_lease_until_stopped(store, "attempt", "worker", _HeartbeatStop([False]), lease_lost)

    assert store.calls == 1
    assert lease_lost.is_set()


def test_branch_confirmation_atomically_creates_submission(tmp_path: Path) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    _git(repo, "checkout", "-b", "feature")

    pending = runtime.submit_user_message(session_id, "  修复   登录问题  ")

    assert pending == BranchConfirmationRequired(session_id, "main", "feature")
    assert service.store.turns.list_turns(session_id) == []
    assert service.store.messages.list_messages_with_parts(session_id) == []
    assert service.store.events.list_events(session_id) == []

    submission = runtime.submit_user_message(session_id, "  修复   登录问题  ", confirmed_branch="feature")

    assert isinstance(submission, TurnSubmission)
    assert submission.turn.status == "queued"
    assert submission.turn.branch_snapshot == "feature"
    assert submission.attempt.attempt_number == 1
    assert submission.attempt.status == "created"
    assert service.store.messages.list_messages_with_parts(session_id)[0][0].content == "  修复   登录问题  "
    assert service.store.sessions.get_session(session_id).title == "修复 登录问题"
    assert service.store.sessions.get_session(session_id).current_branch == "feature"
    assert [event.event_type for event in service.store.events.list_events(session_id)] == [
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
    assert service.store.turns.list_turns(session_id) == []
    assert service.store.messages.list_messages_with_parts(session_id) == []
    assert service.store.events.list_events(session_id) == []
    assert service.store.sessions.get_session(session_id).current_branch == "main"


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

    assert service.store.turns.list_turns(session_id) == []
    assert service.store.messages.list_messages_with_parts(session_id) == []
    assert service.store.events.list_events(session_id) == []
    assert service.store.sessions.get_session(session_id).title == "New session"
    assert service.store.sessions.get_session(session_id).current_branch == "main"


def test_none_branch_can_be_explicitly_confirmed(tmp_path: Path) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    shutil.rmtree(repo / ".git")

    pending = runtime.submit_user_message(session_id, "continue in plain directory")
    submission = runtime.submit_user_message(session_id, "continue in plain directory", confirmed_branch=None)

    assert pending == BranchConfirmationRequired(session_id, "main", None)
    assert isinstance(submission, TurnSubmission)
    assert submission.turn.branch_snapshot is None
    assert service.store.sessions.get_session(session_id).current_branch is None


def test_transaction_rechecks_branch_after_initial_validation(tmp_path: Path, monkeypatch) -> None:
    runtime, service, session_id, repo = _runtime(tmp_path)
    _git(repo, "checkout", "-b", "feature")
    monkeypatch.setattr("codepilot.session.runtime.read_git_context", lambda path: GitContext(True, "feature-2"))

    pending = runtime.submit_user_message(session_id, "fix", confirmed_branch="feature")

    assert pending == BranchConfirmationRequired(session_id, "feature", "feature-2")
    assert service.store.turns.list_turns(session_id) == []
    assert service.store.events.list_events(session_id) == []


class _Cancelled:
    def is_cancelled(self) -> bool:
        return True


class _RaisingLLM:
    def complete(self, messages, *, tools=(), tool_choice="auto"):  # noqa: ANN001
        raise RuntimeError("provider failed")


def test_run_turn_sets_precise_attempt_times_and_terminal_status(tmp_path: Path) -> None:
    _, service, session_id, repo = _runtime(tmp_path)
    runtime = SessionRuntime(service.database, StructuredFakeLLM([LLMResponse(content="hello")]), lambda trace: ToolRouter(trace))
    submission = runtime.submit_user_message(session_id, "say hello")
    assert isinstance(submission, TurnSubmission)

    execution = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)
    attempt = service.store.attempts.get_attempt(submission.attempt.attempt_id)

    assert execution.result.status == "message_complete"
    assert execution.attempt_id == submission.attempt.attempt_id
    assert execution.result.trace_path is None
    assert attempt.status == "completed"
    assert attempt.started_at is not None
    assert attempt.ended_at is not None
    assert service.store.turns.get_turn(submission.turn.turn_id).status == "completed"
    with pytest.raises(RuntimeError, match="created state"):
        runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)
    assert service.store.attempts.get_attempt(submission.attempt.attempt_id).status == "completed"


class _FailingCandidateExtractor:
    def extract(self, session_id: str, turn_id: str):
        raise sqlite3.OperationalError("boom")


class _SuccessfulCandidateExtractor:
    def extract(self, session_id: str, turn_id: str):
        return [SimpleNamespace(candidate_id="candidate-1")]


@pytest.mark.parametrize(
    ("extractor", "event_type", "payload"),
    [
        (_FailingCandidateExtractor(), "memory_candidate_extraction_failed", {"error": "boom"}),
        (
            _SuccessfulCandidateExtractor(),
            "memory_candidates_extracted",
            {"candidate_ids": ["candidate-1"], "count": 1},
        ),
    ],
)
def test_memory_candidate_postprocessing_does_not_change_completed_turn(
    tmp_path: Path,
    extractor,
    event_type: str,
    payload: dict,
) -> None:
    _, service, session_id, _ = _runtime(tmp_path)
    runtime = SessionRuntime(service.database, StructuredFakeLLM([LLMResponse(content="hello")]), lambda trace: ToolRouter(trace))
    runtime.memory_candidates = extractor
    submission = runtime.submit_user_message(session_id, "say hello")
    assert isinstance(submission, TurnSubmission)

    execution = runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    assert execution.result.status == "message_complete"
    assert service.store.turns.get_turn(submission.turn.turn_id).status == "completed"
    assert service.store.attempts.get_attempt(submission.attempt.attempt_id).status == "completed"
    event = next(event for event in service.store.events.list_events(session_id) if event.event_type == event_type)
    assert event.payload == payload


def test_submit_user_message_blocks_recovery_required_turns(tmp_path: Path) -> None:
    _, service, session_id, _ = _runtime(tmp_path)
    turn = service.store.turns.create_turn(
        session_id=session_id,
        title="Turn 1",
        provider_snapshot="openai",
        model_snapshot="gpt-4.1",
        permission_mode_snapshot="manual",
        branch_snapshot="main",
    )
    service.store.turns.update_turn_status(turn.turn_id, "recovery_required")
    runtime = SessionRuntime(service.database, StructuredFakeLLM([LLMResponse(content="hello")]), lambda trace: ToolRouter(trace))

    with pytest.raises(RuntimeError, match="running turn"):
        runtime.submit_user_message(session_id, "new task")


def test_run_turn_maps_cancelled_and_llm_error_explicitly(tmp_path: Path) -> None:
    _, service, session_id, _ = _runtime(tmp_path)
    cancelled_runtime = SessionRuntime(service.database, StructuredFakeLLM([LLMResponse(content="unused")]), lambda trace: ToolRouter(trace))
    cancelled = cancelled_runtime.submit_user_message(session_id, "cancel")
    assert isinstance(cancelled, TurnSubmission)
    result = cancelled_runtime.run_turn(cancelled.turn.turn_id, cancelled.attempt.attempt_id, _Cancelled())
    assert result.result.status == "cancelled"
    assert service.store.attempts.get_attempt(cancelled.attempt.attempt_id).status == "cancelled"
    assert service.store.turns.get_turn(cancelled.turn.turn_id).status == "cancelled"

    failed_runtime = SessionRuntime(service.database, _RaisingLLM(), lambda trace: ToolRouter(trace))
    failed = failed_runtime.submit_user_message(session_id, "fail")
    assert isinstance(failed, TurnSubmission)
    result = failed_runtime.run_turn(failed.turn.turn_id, failed.attempt.attempt_id)
    assert result.result.status == "llm_error"
    assert service.store.attempts.get_attempt(failed.attempt.attempt_id).status == "failed"
    assert service.store.turns.get_turn(failed.turn.turn_id).status == "failed"


def test_run_turn_setup_exception_is_interrupted(tmp_path: Path) -> None:
    _, service, session_id, _ = _runtime(tmp_path)

    def broken_router(trace):  # noqa: ANN001
        raise RuntimeError("router setup failed")

    runtime = SessionRuntime(service.database, StructuredFakeLLM([LLMResponse(content="unused")]), broken_router)
    submission = runtime.submit_user_message(session_id, "fail setup")
    assert isinstance(submission, TurnSubmission)

    with pytest.raises(RuntimeError, match="router setup failed"):
        runtime.run_turn(submission.turn.turn_id, submission.attempt.attempt_id)

    attempt = service.store.attempts.get_attempt(submission.attempt.attempt_id)
    assert attempt.status == "interrupted"
    assert attempt.interruption_reason == "router setup failed"
    assert service.store.turns.get_turn(submission.turn.turn_id).status == "interrupted"
