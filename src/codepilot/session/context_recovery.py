from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.memory.policy import redact_memory_value
from codepilot.memory.turn_window import TurnContextWindow
from codepilot.session.compaction import CompactionService
from codepilot.session.context import ContextAssembler
from codepilot.session.context_adapters import PreparedContext
from codepilot.session.context_audit import ContextAuditRepository
from codepilot.session.context_budget import ContextBudgetExceeded, ContextItem, estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore


@dataclass(frozen=True)
class ContextRecoveryResult:
    messages: list[ChatMessage | RichChatMessage]
    base_message_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    selected_context_items: tuple[ContextItem, ...] = ()
    omitted_context_items: tuple[ContextItem, ...] = ()


class SessionContextRecoveryCoordinator:
    def __init__(self, database: SessionDatabase, compaction_service: CompactionService, assembler: ContextAssembler, profile: ModelContextProfile) -> None:
        self.store = SessionStore(database)
        self.compaction_service = compaction_service
        self.assembler = assembler
        self.profile = profile
        self.window = TurnContextWindow(database, profile)
        self.audit = ContextAuditRepository(database)

    def recover_from_provider_overflow(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        attempt_id: str | None,
        step: int,
        task: str,
        evidence: dict[str, Any],
        original_messages: list[ChatMessage | RichChatMessage],
        original_base_message_count: int,
        error: BaseException,
    ) -> ContextRecoveryResult:
        if session_id is None or turn_id is None:
            raise RuntimeError("provider overflow recovery requires a persisted Session")
        self._event(session_id, turn_id, attempt_id, "provider_context_overflow", step, error=error)
        self._event(session_id, turn_id, attempt_id, "provider_context_overflow_recovery_started", step)
        original_tokens = sum(estimate_tokens(message) for message in original_messages) + self.profile.protocol_overhead_tokens
        summary_id = None
        after_session_tokens = original_tokens
        try:
            try:
                compacted = self.compaction_service.compact(
                    session_id,
                    force=True,
                    trigger="provider_overflow",
                    audit=False,
                    current_turn_id=turn_id,
                    profile=self.profile,
                )
                summary_id = compacted.summary.summary_id
            except ContextBudgetExceeded as exc:
                if exc.reason != "no_compactable_history":
                    raise
            base_prepared = self.assembler.build_with_manifest(session_id, turn_id, self.profile.provider, self.profile.model, profile=self.profile)
            after_session_tokens = base_prepared.estimated_tokens
            prepared = self.window.recover_from_provider_overflow(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                step=step,
                messages=base_prepared.messages,
                base_message_count=len(base_prepared.messages),
                task=task,
                evidence=evidence,
                audit=False,
                selected_context_items=base_prepared.selected_items,
                omitted_context_items=base_prepared.omitted_items,
            )
        except Exception as exc:
            self._event(session_id, turn_id, attempt_id, "provider_context_overflow_recovery_failed", step, error=exc)
            try:
                self.audit.record(
                    session_id=session_id,
                    turn_id=turn_id,
                    attempt_id=attempt_id,
                    step=step,
                    trigger="provider_overflow",
                    scope="provider_overflow",
                    status="failed",
                    summary_id=summary_id,
                    estimated_tokens_before=original_tokens,
                    estimated_tokens_after=0,
                    protocol_overhead_tokens=self.profile.protocol_overhead_tokens,
                    max_input_tokens=self.profile.max_input_tokens,
                    metadata={
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "original_request_tokens": original_tokens,
                        "after_session_compaction_tokens": after_session_tokens,
                        "original_base_message_count": original_base_message_count,
                        "retry_number": 1,
                    },
                )
            except Exception:
                pass
            raise
        snapshot_id = None
        try:
            snapshot_id = self.audit.record(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                step=step,
                trigger="provider_overflow",
                scope="provider_overflow",
                status="completed",
                summary_id=summary_id,
                checkpoint_id=prepared.checkpoint_id,
                estimated_tokens_before=original_tokens,
                estimated_tokens_after=prepared.estimated_tokens_after,
                protocol_overhead_tokens=self.profile.protocol_overhead_tokens,
                max_input_tokens=self.profile.max_input_tokens,
                prepared=PreparedContext(
                    prepared.messages,
                    prepared.selected_context_items,
                    prepared.omitted_context_items,
                    prepared.estimated_tokens_after,
                    base_prepared.metadata,
                ),
                messages=prepared.messages,
                message_sources=prepared.message_sources,
                covered_message_ids=prepared.covered_message_ids,
                metadata={
                    "original_request_tokens": original_tokens,
                    "after_session_compaction_tokens": after_session_tokens,
                    "after_turn_checkpoint_tokens": prepared.estimated_tokens_after,
                    "retry_number": 1,
                    "session_summary_id": summary_id,
                    "turn_checkpoint_id": prepared.checkpoint_id,
                    "original_base_message_count": original_base_message_count,
                },
            ).snapshot_id
        except Exception:
            pass
        self._event(
            session_id,
            turn_id,
            attempt_id,
            "provider_context_overflow_recovery_completed",
            step,
            extra={"estimated_tokens_before": original_tokens, "estimated_tokens_after": prepared.estimated_tokens_after, "checkpoint_id": prepared.checkpoint_id, "snapshot_id": snapshot_id},
        )
        return ContextRecoveryResult(
            prepared.messages,
            prepared.base_message_count,
            {
                "checkpoint_id": prepared.checkpoint_id,
                "estimated_tokens_before": original_tokens,
                "estimated_tokens_after": prepared.estimated_tokens_after,
                "snapshot_id": snapshot_id,
            },
            prepared.selected_context_items,
            prepared.omitted_context_items,
        )

    def retry_exhausted(self, *, session_id: str | None, turn_id: str | None, attempt_id: str | None, step: int, error: BaseException) -> None:
        if session_id is not None and turn_id is not None:
            self._event(session_id, turn_id, attempt_id, "provider_context_overflow_retry_exhausted", step, error=error)

    def _event(self, session_id: str, turn_id: str, attempt_id: str | None, event_type: str, step: int, *, error: BaseException | None = None, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"step": step, "provider": self.profile.provider, "model": self.profile.model, "retry_number": 1}
        if error is not None:
            payload.update({"error_type": type(error).__name__, "error": str(redact_memory_value(str(error)).value), "output_started": bool(getattr(error, "output_started", False))})
        payload.update(extra or {})
        self.store.append_event(session_id=session_id, turn_id=turn_id, attempt_id=attempt_id, event_type=event_type, payload=payload, metadata={"source": "context_recovery"})
