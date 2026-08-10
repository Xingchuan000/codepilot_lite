from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.memory.models import TurnCheckpointContent
from codepilot.memory.policy import sanitize_memory_content
from codepilot.memory.repository import TurnCheckpointRepository
from codepilot.memory.turn_checkpoint_builder import TurnCheckpointBuilder
from codepilot.session.context_adapters import PreparedContext
from codepilot.session.context_audit import ContextAuditRepository, ContextMessageSource
from codepilot.session.context_budget import ContextBudgetExceeded, ContextItem, estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore

CHECKPOINT_PREFIX = "Current turn rolling checkpoint."


@dataclass(frozen=True)
class ContextPreparationResult:
    messages: list[ChatMessage | RichChatMessage]
    base_message_count: int
    compacted: bool = False
    trigger: str = "soft_budget"
    checkpoint_id: str | None = None
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
    covered_message_ids: tuple[str, ...] = ()
    retained_message_count: int = 0
    selected_context_items: tuple[ContextItem, ...] = ()
    omitted_context_items: tuple[ContextItem, ...] = ()
    message_sources: tuple[ContextMessageSource, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Any]:
        yield self.messages
        yield self.base_message_count


@dataclass(frozen=True)
class TurnMessageGroup:
    messages: tuple[ChatMessage | RichChatMessage, ...]
    kind: Literal["tool_exchange", "assistant_reply", "other"]
    estimated_tokens: int


@dataclass(frozen=True)
class WindowFitResult:
    messages: list[ChatMessage | RichChatMessage]
    selected_groups: tuple[TurnMessageGroup, ...]
    omitted_groups: tuple[TurnMessageGroup, ...]
    estimated_tokens: int


class TurnContextWindow:
    """Persist and apply structured rolling checkpoints before each LLM call."""

    def __init__(
        self,
        database: SessionDatabase,
        profile: ModelContextProfile,
        *,
        soft_limit: float = 0.7,
        recent_group_count: int = 3,
    ) -> None:
        if not 0 < soft_limit <= 1:
            raise ValueError("soft_limit must be between 0 and 1")
        self.store = SessionStore(database)
        self.checkpoints = TurnCheckpointRepository(database)
        self.builder = TurnCheckpointBuilder(database)
        self.audit = ContextAuditRepository(database)
        self.profile = profile
        self.soft_limit = soft_limit
        self.recent_group_count = recent_group_count

    def prepare_for_llm(self, **kwargs: Any) -> ContextPreparationResult:
        return self._prepare_with_failure_audit(force=False, trigger="soft_budget", recent_group_count=self.recent_group_count, audit=True, **kwargs)

    def recover_from_provider_overflow(self, **kwargs: Any) -> ContextPreparationResult:
        audit = bool(kwargs.pop("audit", False))
        return self._prepare_with_failure_audit(force=True, trigger="provider_overflow", recent_group_count=1, audit=audit, **kwargs)

    def _prepare_with_failure_audit(self, *, trigger: str, audit: bool, **kwargs: Any) -> ContextPreparationResult:
        try:
            return self._prepare(trigger=trigger, audit=audit, **kwargs)
        except Exception as exc:
            session_id = kwargs.get("session_id")
            turn_id = kwargs.get("turn_id")
            messages = kwargs.get("messages", [])
            if audit and isinstance(session_id, str) and isinstance(turn_id, str):
                try:
                    self.audit.record(
                        session_id=session_id,
                        turn_id=turn_id,
                        attempt_id=kwargs.get("attempt_id"),
                        step=kwargs.get("step"),
                        trigger=trigger,
                        scope="provider_overflow" if trigger == "provider_overflow" else "turn",
                        status="failed",
                        estimated_tokens_before=_tokens(messages) + self.profile.protocol_overhead_tokens,
                        estimated_tokens_after=0,
                        protocol_overhead_tokens=self.profile.protocol_overhead_tokens,
                        max_input_tokens=self.profile.max_input_tokens,
                        messages=messages,
                        metadata={"error_type": type(exc).__name__, "error": str(exc)},
                    )
                except Exception:
                    pass
            raise

    def _prepare(
        self,
        *,
        session_id: str | None,
        turn_id: str | None,
        attempt_id: str | None,
        step: int,
        messages: list[ChatMessage | RichChatMessage],
        base_message_count: int,
        task: str,
        evidence: dict[str, Any],
        force: bool,
        trigger: str,
        recent_group_count: int,
        audit: bool,
        selected_context_items: tuple[ContextItem, ...] = (),
        omitted_context_items: tuple[ContextItem, ...] = (),
    ) -> ContextPreparationResult:
        before = _tokens(messages) + self.profile.protocol_overhead_tokens
        unchanged = ContextPreparationResult(
            messages,
            base_message_count,
            trigger=trigger,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
            selected_context_items=selected_context_items,
            omitted_context_items=omitted_context_items,
        )
        if session_id is None or turn_id is None:
            return unchanged
        previous = self.checkpoints.latest(turn_id)
        if base_message_count == len(messages):
            base_message_count = _recovered_base_message_count(
                self.store,
                session_id,
                turn_id,
                messages,
                previous.covered_message_ids if previous is not None else (),
            )
        if not force and before <= self.profile.max_input_tokens * self.soft_limit:
            return replace(unchanged, base_message_count=base_message_count)
        base = [message for message in messages[:base_message_count] if not _is_checkpoint(message)]
        dynamic = [message for message in messages[base_message_count:] if not _is_checkpoint(message)]
        groups = _message_groups(dynamic)
        covered_groups = groups[:-recent_group_count]
        if not covered_groups:
            if not force and before <= self.profile.max_input_tokens:
                return replace(unchanged, base_message_count=base_message_count)
            covered_groups = groups[:-1] if len(groups) > 1 else groups
        candidate_retained_groups = groups[len(covered_groups):]
        previous_covered_ids = previous.covered_message_ids if previous is not None else ()
        for _ in range(len(groups) + 1):
            planned_covered_groups = groups[:-len(candidate_retained_groups)] if candidate_retained_groups else groups
            covered_ids = _covered_message_ids(
                self.store,
                session_id,
                turn_id,
                sum(len(group.messages) for group in candidate_retained_groups),
                previous_covered_ids,
            )
            content = self.builder.build(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                step=step,
                task=task,
                evidence=evidence,
                covered_message_ids=covered_ids,
                previous=previous,
            )
            if not content.recent_tool_facts:
                content = replace(content, fallback_previews=_bounded_previews([
                    *(content.fallback_previews), *(_group_preview(group) for group in planned_covered_groups)
                ], max(128, int(self.profile.max_input_tokens * 0.08) * 4)))
            content = _fit_checkpoint_content(content, max(256, int(self.profile.max_input_tokens * 0.08)))
            sanitized, redaction_count = sanitize_memory_content(content.to_dict())
            checkpoint = self.checkpoints.replace(
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                step=step,
                content=sanitized,
                covered_message_ids=covered_ids,
                model=self.profile.model,
                metadata={
                    "schema_version": 2,
                    "trigger": trigger,
                    "soft_limit": self.soft_limit,
                    "recent_group_count": recent_group_count,
                    "estimated_tokens_before": before,
                    "redaction_count": redaction_count,
                },
            )
            checkpoint_message = ChatMessage("system", render_turn_checkpoint(checkpoint.content))
            fit = _fit_to_window(base, checkpoint_message, candidate_retained_groups, self.profile)
            if not fit.omitted_groups:
                break
            candidate_retained_groups = fit.selected_groups
        else:
            raise RuntimeError("turn context fitting did not converge")
        prepared = fit.messages
        after = fit.estimated_tokens + self.profile.protocol_overhead_tokens
        retained_ids = _retained_message_ids(self.store, turn_id, covered_ids)
        _assert_coverage_invariant(self.store, turn_id, covered_ids, retained_ids)
        checkpoint_item = ContextItem(
            key=f"turn-checkpoint-{checkpoint.checkpoint_id}",
            messages=(checkpoint_message,),
            estimated_tokens=estimate_tokens(checkpoint_message),
            mandatory=True,
            priority=880,
            source_kind="turn_checkpoint",
            source_ids=(checkpoint.checkpoint_id,),
        )
        dynamic_ids = _dynamic_message_ids(self.store, turn_id)
        dynamic_source_ids = _context_source_ids(self.store, turn_id, dynamic_ids)
        retained_source_ids = _context_source_ids(self.store, turn_id, tuple(message_id for message_id in retained_ids if message_id in dynamic_ids))
        base_items = tuple(
            item
            for item in selected_context_items
            if item.source_kind != "turn_checkpoint" and not set(item.source_ids) & dynamic_source_ids
        )
        retained_items = tuple(
            item
            for item in selected_context_items
            if item.source_kind != "turn_checkpoint" and set(item.source_ids) & retained_source_ids
        )
        selected_items = (*base_items, checkpoint_item, *retained_items)
        message_sources = _message_sources(selected_items, retained_ids, prepared)
        prepared_context = PreparedContext(
            prepared,
            selected_items,
            omitted_context_items,
            after,
            {"max_input_tokens": self.profile.max_input_tokens, "protocol_overhead_tokens": self.profile.protocol_overhead_tokens},
        )
        summary_id = next((source_id for item in selected_items if item.source_kind == "summary" for source_id in item.source_ids), None)
        snapshot_id = None
        if audit:
            try:
                snapshot = self.audit.record(
                    session_id=session_id,
                    turn_id=turn_id,
                    attempt_id=attempt_id,
                    step=step,
                    trigger=trigger,
                    scope="turn",
                    status="completed",
                    summary_id=summary_id,
                    checkpoint_id=checkpoint.checkpoint_id,
                    estimated_tokens_before=before,
                    estimated_tokens_after=after,
                    protocol_overhead_tokens=self.profile.protocol_overhead_tokens,
                    max_input_tokens=self.profile.max_input_tokens,
                    prepared=prepared_context,
                    messages=prepared,
                    message_sources=message_sources,
                    covered_message_ids=covered_ids,
                    retained_message_ids=retained_ids,
                    metadata={"recent_group_count": recent_group_count},
                )
                snapshot_id = snapshot.snapshot_id
            except Exception:
                pass
        return ContextPreparationResult(
            prepared,
            len(base) + 1,
            compacted=True,
            trigger=trigger,
            checkpoint_id=checkpoint.checkpoint_id,
            estimated_tokens_before=before,
            estimated_tokens_after=after,
            covered_message_ids=covered_ids,
            retained_message_count=len(retained_ids),
            selected_context_items=selected_items,
            omitted_context_items=omitted_context_items,
            message_sources=message_sources,
            metadata={"redaction_count": redaction_count, "recent_group_count": recent_group_count, "snapshot_id": snapshot_id},
        )


def render_turn_checkpoint(value: dict[str, Any]) -> str:
    content = TurnCheckpointContent.from_dict(value)
    lines = [CHECKPOINT_PREFIX, f"Current goal: {content.current_goal}", f"Current step: {content.current_step}"]
    sections: tuple[tuple[str, tuple[Any, ...]], ...] = (
        ("User constraints", content.user_constraints),
        ("Confirmed decisions", content.confirmed_decisions),
        ("Files read", content.files_read),
        ("Files modified", content.files_modified),
        ("Commands run", content.commands_run),
        ("Test results", content.test_results),
        ("Current errors", content.current_errors),
        ("Pending tool calls", content.pending_tool_calls),
        ("Recent tool facts", content.recent_tool_facts),
        ("Fallback previews", content.fallback_previews),
    )
    for title, items in sections:
        if items:
            lines.extend((f"\n{title}:", *(f"- {item}" for item in items)))
    if content.evidence:
        lines.extend(("\nVerified evidence:", f"- {content.evidence}"))
    if content.next_step:
        lines.extend(("\nNext step:", f"- {content.next_step}"))
    return "\n".join(lines)


def _message_groups(messages: list[ChatMessage | RichChatMessage]) -> list[TurnMessageGroup]:
    groups: list[TurnMessageGroup] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        paired: tuple[ChatMessage | RichChatMessage, ...] = (message,)
        kind: Literal["tool_exchange", "assistant_reply", "other"] = "assistant_reply" if message.role == "assistant" else "other"
        if message.role == "assistant" and index + 1 < len(messages):
            following = messages[index + 1]
            if isinstance(message, RichChatMessage) and message.parts and following.role == "tool" and isinstance(following, RichChatMessage):
                paired, kind = (message, following), "tool_exchange"
        groups.append(TurnMessageGroup(paired, kind, _tokens(list(paired))))
        index += len(paired)
    return groups


def _fit_checkpoint_content(content: TurnCheckpointContent, token_budget: int) -> TurnCheckpointContent:
    candidate = content
    if estimate_tokens(render_turn_checkpoint(candidate.to_dict())) <= token_budget:
        return candidate
    candidate = replace(candidate, recent_tool_facts=candidate.recent_tool_facts[-4:])
    candidate = replace(candidate, fallback_previews=candidate.fallback_previews[-3:])
    fields = ("user_constraints", "confirmed_decisions", "files_read", "files_modified", "commands_run", "test_results")
    candidate = replace(candidate, **{name: getattr(candidate, name)[-4:] for name in fields})
    if estimate_tokens(render_turn_checkpoint(candidate.to_dict())) <= token_budget:
        return candidate
    candidate = replace(candidate, current_errors=candidate.current_errors[-3:])
    candidate = replace(
        candidate,
        pending_tool_calls=tuple(
            {
                key: value
                for key, value in item.items()
                if key in {"tool_call_id", "tool_name", "status"}
            }
            for item in candidate.pending_tool_calls[-8:]
        ),
    )
    candidate = replace(
        candidate,
        evidence={
            key: candidate.evidence[key]
            for key in ("missing", "diff_checked", "write_executed", "tests_required")
            if key in candidate.evidence
        },
        current_goal=candidate.current_goal[:400],
        next_step=candidate.next_step[:240],
    )
    if estimate_tokens(render_turn_checkpoint(candidate.to_dict())) <= token_budget:
        return candidate
    candidate = TurnCheckpointContent(
        current_goal=content.current_goal[:240],
        current_step=content.current_step,
        pending_tool_calls=tuple(
            {
                key: value
                for key, value in item.items()
                if key in {"tool_call_id", "tool_name", "status"}
            }
            for item in content.pending_tool_calls[-3:]
        ),
        next_step=content.next_step[:200],
    )
    if estimate_tokens(render_turn_checkpoint(candidate.to_dict())) > token_budget:
        raise ContextBudgetExceeded("structured turn checkpoint exceeds its budget", reason="turn_checkpoint_overflow")
    return candidate


def _tokens(messages: list[ChatMessage | RichChatMessage]) -> int:
    return sum(estimate_tokens(message) for message in messages)


def _is_checkpoint(message: ChatMessage | RichChatMessage) -> bool:
    return isinstance(message, ChatMessage) and message.content.startswith(CHECKPOINT_PREFIX)


def _group_preview(group: TurnMessageGroup) -> str:
    return " | ".join(f"{message.role}: {message.content[:400]}" if isinstance(message, ChatMessage) else f"{message.role}: {str(message.parts)[:400]}" for message in group.messages)


def _bounded_previews(previews: list[str] | tuple[str, ...], max_chars: int) -> tuple[str, ...]:
    selected: list[str] = []
    remaining = max_chars
    for preview in reversed(previews):
        if remaining <= 0:
            break
        selected.append(preview[:remaining])
        remaining -= len(selected[-1])
    return tuple(reversed(selected))


def _covered_message_ids(store: SessionStore, session_id: str, turn_id: str, retained_count: int, previous: tuple[str, ...]) -> tuple[str, ...]:
    messages = [message for message, _ in _turn_dynamic_messages(store, turn_id)]
    newly_covered = messages[:-retained_count] if retained_count else messages
    return tuple(dict.fromkeys([*previous, *(message.message_id for message in newly_covered)]))


def _recovered_base_message_count(store: SessionStore, session_id: str, turn_id: str, messages: list[ChatMessage | RichChatMessage], covered_message_ids: tuple[str, ...]) -> int:
    user_message = store.get_user_message_for_turn(turn_id)
    recent_count = sum(1 for message, _ in store.list_messages_with_parts(session_id) if message.turn_id == turn_id and message.status not in {"failed", "in_progress"} and message.message_id not in covered_message_ids and message.metadata.get("summary_id") is None and (user_message is None or message.message_id != user_message.message_id))
    return max(0, len(messages) - recent_count)


def _retained_message_ids(store: SessionStore, turn_id: str, covered_message_ids: tuple[str, ...]) -> tuple[str, ...]:
    covered = set(covered_message_ids)
    return tuple(message.message_id for message, _ in _turn_dynamic_messages(store, turn_id) if message.message_id not in covered)


def _dynamic_message_ids(store: SessionStore, turn_id: str) -> tuple[str, ...]:
    return tuple(message.message_id for message, _ in _turn_dynamic_messages(store, turn_id))


def _turn_dynamic_messages(store: SessionStore, turn_id: str) -> tuple[tuple[Any, tuple[Any, ...]], ...]:
    user = store.get_user_message_for_turn(turn_id)
    return tuple(
        (message, parts)
        for message, parts in store.list_messages_with_parts(store.get_turn(turn_id).session_id, turn_id)
        if message.status in {"completed", "interrupted"}
        and message.metadata.get("summary_id") is None
        and (user is None or message.message_id != user.message_id)
    )


def _assert_coverage_invariant(
    store: SessionStore,
    turn_id: str,
    covered_message_ids: tuple[str, ...],
    retained_message_ids: tuple[str, ...],
) -> None:
    dynamic_ids = set(_dynamic_message_ids(store, turn_id))
    covered = set(covered_message_ids) & dynamic_ids
    retained = set(retained_message_ids) & dynamic_ids
    if dynamic_ids != covered | retained or covered & retained:
        raise RuntimeError("turn_context_coverage_invariant_failed")


def _context_source_ids(store: SessionStore, turn_id: str, message_ids: tuple[str, ...]) -> set[str]:
    selected = set(message_ids)
    source_ids = set(selected)
    for message, parts in store.list_messages_with_parts(store.get_turn(turn_id).session_id, turn_id):
        if message.message_id not in selected:
            continue
        if message.metadata.get("tool_call_id") is not None:
            source_ids.add(str(message.metadata["tool_call_id"]))
        source_ids.update(str(part.metadata["tool_call_id"]) for part in parts if part.metadata.get("tool_call_id") is not None)
    source_ids.update(call.tool_call_id for call in store.list_tool_calls(store.get_turn(turn_id).session_id) if call.turn_id == turn_id and call.message_id in selected)
    return source_ids


def _message_sources(
    selected_items: tuple[ContextItem, ...],
    retained_message_ids: tuple[str, ...],
    messages: list[ChatMessage | RichChatMessage],
) -> tuple[ContextMessageSource, ...]:
    sources = [
        ContextMessageSource(
            message.role,
            item.source_kind or "generated",
            item.source_ids[0] if item.source_ids else None,
            item.key,
        )
        for item in selected_items
        for message in item.messages
    ]
    selected_source_ids = {source_id for item in selected_items for source_id in item.source_ids}
    sources.extend(
        ContextMessageSource("", "message", message_id, f"message-{message_id}")
        for message_id in retained_message_ids
        if message_id not in selected_source_ids
    )
    return tuple(
        ContextMessageSource(message.role, source.source_kind, source.source_id, source.context_key)
        for message, source in zip(messages, sources, strict=False)
    )


def _fit_to_window(
    base: list[ChatMessage | RichChatMessage],
    checkpoint: ChatMessage | None,
    groups: list[TurnMessageGroup] | tuple[TurnMessageGroup, ...],
    profile: ModelContextProfile,
) -> WindowFitResult:
    fixed = [*base, *([checkpoint] if checkpoint is not None else [])]
    limit = profile.max_input_tokens - profile.protocol_overhead_tokens
    used = _tokens(fixed)
    if used >= limit:
        raise ContextBudgetExceeded("turn checkpoint does not fit beside mandatory context", reason="turn_checkpoint_overflow")
    selected: list[TurnMessageGroup] = []
    for group in reversed(groups):
        if used + group.estimated_tokens <= limit:
            selected.append(group)
            used += group.estimated_tokens
            continue
        break
    selected_groups = tuple(reversed(selected))
    omitted_groups = tuple(groups[:-len(selected_groups)]) if selected_groups else tuple(groups)
    result = [*fixed, *(message for group in selected_groups for message in group.messages)]
    if _tokens(result) > limit:
        raise ContextBudgetExceeded("current turn cannot fit in the model input budget", reason="turn_window_overflow")
    return WindowFitResult(result, selected_groups, omitted_groups, _tokens(result))
