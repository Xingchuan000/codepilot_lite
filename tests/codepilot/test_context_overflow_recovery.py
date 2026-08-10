from pathlib import Path

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.llm.errors import LLMContextOverflowError
from codepilot.llm.types import ChatMessage, LLMResponse, LLMToolCall
from codepilot.router import ToolRouter
from codepilot.session.context import ContextAssembler
from codepilot.session.context_audit import ContextAuditRepository
from codepilot.session.context_budget import ContextBudgetExceeded, estimate_tokens
from codepilot.session.context_recovery import ContextRecoveryResult, SessionContextRecoveryCoordinator
from codepilot.session.database import SessionDatabase
from codepilot.session.model_context import ModelContextProfile
from codepilot.session.repositories import SessionRepositories


class _OverflowThenFinish:
    def __init__(self, *, always: bool = False) -> None:
        self.calls = 0
        self.always = always

    def complete(self, messages, *, tools=(), tool_choice="auto"):
        self.calls += 1
        if self.calls == 1 or self.always:
            raise LLMContextOverflowError("maximum context length", output_started=False)
        return LLMResponse(
            content="",
            tool_calls=(LLMToolCall("provider-finish", "codepilot_finish", {"status": "partial", "summary": "recovered"}),),
        )


class _Recovery:
    def __init__(self) -> None:
        self.calls = 0
        self.exhausted = 0

    def recover_from_provider_overflow(self, **values):
        self.calls += 1
        self.original_messages = values["original_messages"]
        self.original_base_message_count = values["original_base_message_count"]
        return ContextRecoveryResult(
            messages=[ChatMessage("system", "short"), ChatMessage("user", values["task"])],
            base_message_count=2,
        )

    def retry_exhausted(self, **values):
        self.exhausted += 1


def test_overflow_retries_once_without_incrementing_agent_step(tmp_path: Path) -> None:
    llm = _OverflowThenFinish()
    recovery = _Recovery()
    result = MinimalAgentLoop(llm=llm, router=ToolRouter.from_runs_dir(tmp_path / "runs"), context_recovery=recovery).run("inspect", tmp_path)

    assert result.status == "partial"
    assert result.steps == 1
    assert llm.calls == 2
    assert recovery.calls == 1
    assert recovery.original_messages
    assert recovery.original_base_message_count == len(recovery.original_messages)


def test_second_overflow_is_not_retried_a_third_time(tmp_path: Path) -> None:
    llm = _OverflowThenFinish(always=True)
    recovery = _Recovery()
    result = MinimalAgentLoop(llm=llm, router=ToolRouter.from_runs_dir(tmp_path / "runs"), context_recovery=recovery).run("inspect", tmp_path)

    assert result.status == "llm_error"
    assert llm.calls == 2
    assert recovery.exhausted == 1


class _PartialNativeResponse:
    def complete(self, messages, *, tools=(), tool_choice="auto"):
        raise LLMContextOverflowError("maximum context length", output_started=True)


def test_partial_stream_output_disables_overflow_retry(tmp_path: Path) -> None:
    recovery = _Recovery()
    result = MinimalAgentLoop(llm=_PartialNativeResponse(), router=ToolRouter.from_runs_dir(tmp_path / "runs"), context_recovery=recovery).run("inspect", tmp_path)

    assert result.status == "llm_error"
    assert recovery.calls == 0


class _NoHistoryCompaction:
    def __init__(self) -> None:
        self.values = {}

    def compact(self, session_id, **values):
        self.values = values
        raise ContextBudgetExceeded("none", reason="no_compactable_history")


def test_recovery_records_one_aggregate_snapshot_with_original_request_tokens(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    session = store.sessions.create_session(project_path=tmp_path, provider="openai", current_model="tiny", permission_mode="manual")
    turn = store.turns.create_turn(session_id=session.session_id, title="overflow", provider_snapshot="openai", model_snapshot="tiny", permission_mode_snapshot="manual", branch_snapshot=None)
    store.messages.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content="inspect")
    profile = ModelContextProfile("openai", "tiny", 16_384, False, protocol_overhead_tokens=11)
    compaction = _NoHistoryCompaction()
    coordinator = SessionContextRecoveryCoordinator(database, compaction, ContextAssembler(database), profile)
    original = [ChatMessage("system", "x" * 2000), ChatMessage("user", "inspect")]

    result = coordinator.recover_from_provider_overflow(
        session_id=session.session_id, turn_id=turn.turn_id, attempt_id=None, step=1, task="inspect",
        evidence={}, original_messages=original, original_base_message_count=2,
        error=LLMContextOverflowError("prompt is too long", output_started=False),
    )
    snapshots = ContextAuditRepository(database).list_for_turn(turn.turn_id)

    assert len(snapshots) == 1
    assert snapshots[0].scope == "provider_overflow"
    assert snapshots[0].trigger == "provider_overflow"
    assert snapshots[0].estimated_tokens_before == sum(estimate_tokens(message) for message in original) + 11
    assert snapshots[0].estimated_tokens_after == sum(estimate_tokens(message) for message in result.messages) + 11
    assert compaction.values["trigger"] == "provider_overflow"
    assert compaction.values["audit"] is False

