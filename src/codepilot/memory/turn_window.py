from __future__ import annotations

from typing import Any

from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.memory.repository import TurnCheckpointRepository
from codepilot.session.context_budget import ContextBudgetExceeded, estimate_tokens
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore

CHECKPOINT_PREFIX = "Current turn rolling checkpoint."


class TurnContextWindow:
    """Persist and apply rolling checkpoints before each LLM call."""

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
        self.profile = profile
        self.soft_limit = soft_limit
        self.recent_group_count = recent_group_count

    def prepare_for_llm(
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
    ) -> tuple[list[ChatMessage | RichChatMessage], int]:
        if session_id is None or turn_id is None:
            return messages, base_message_count
        previous = self.checkpoints.latest(turn_id)
        if previous is not None and base_message_count == len(messages):
            base_message_count = _recovered_base_message_count(
                self.store,
                session_id,
                turn_id,
                messages,
                previous.covered_message_ids,
            )
        if _tokens(messages) + self.profile.protocol_overhead_tokens <= self.profile.max_input_tokens * self.soft_limit:
            return messages, base_message_count
        base = [message for message in messages[:base_message_count] if not _is_checkpoint(message)]
        dynamic = [message for message in messages[base_message_count:] if not _is_checkpoint(message)]
        groups = _message_groups(dynamic)
        covered_groups = groups[:-self.recent_group_count]
        if not covered_groups:
            if _tokens(messages) + self.profile.protocol_overhead_tokens <= self.profile.max_input_tokens:
                return messages, base_message_count
            covered_groups = groups[:-1] if len(groups) > 1 else groups
        checkpoint_content = {
            "task_goal": task,
            "step": step,
            "earlier_exchanges": [
                *(
                    previous.content.get("earlier_exchanges", [])
                    if previous is not None
                    else []
                ),
                *[_group_preview(group) for group in covered_groups],
            ],
            "evidence": evidence,
        }
        checkpoint_content["earlier_exchanges"] = _bounded_previews(
            checkpoint_content["earlier_exchanges"],
            max(128, int(self.profile.max_input_tokens * 0.08) * 4),
        )
        retained_groups = groups[len(covered_groups) :]
        covered_ids = _covered_message_ids(
            self.store,
            session_id,
            turn_id,
            sum(len(group) for group in retained_groups),
            previous.covered_message_ids if previous is not None else (),
        )
        checkpoint = self.checkpoints.replace(
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            step=step,
            content=checkpoint_content,
            covered_message_ids=covered_ids,
            model=self.profile.model,
            metadata={"soft_limit": self.soft_limit, "recent_group_count": self.recent_group_count},
        )
        checkpoint_message = ChatMessage("system", render_turn_checkpoint(checkpoint.content))
        prepared = _fit_to_window(base, checkpoint_message, retained_groups, self.profile)
        return prepared, len(base) + 1


def render_turn_checkpoint(content: dict[str, Any]) -> str:
    lines = [
        CHECKPOINT_PREFIX,
        f"Task: {content.get('task_goal', '')}",
        f"Step: {content.get('step', '')}",
        "Earlier exchanges:",
    ]
    lines.extend(f"- {item}" for item in content.get("earlier_exchanges", []))
    evidence = content.get("evidence", {})
    if evidence:
        lines.append(f"Current verified evidence: {evidence}")
    return "\n".join(lines)


def _tokens(messages: list[ChatMessage | RichChatMessage]) -> int:
    return sum(estimate_tokens(message) for message in messages)


def _is_checkpoint(message: ChatMessage | RichChatMessage) -> bool:
    return isinstance(message, ChatMessage) and message.content.startswith(CHECKPOINT_PREFIX)


def _message_groups(messages: list[ChatMessage | RichChatMessage]) -> list[list[ChatMessage | RichChatMessage]]:
    return [messages[index : index + 2] for index in range(0, len(messages), 2)]


def _group_preview(messages: list[ChatMessage | RichChatMessage]) -> str:
    return " | ".join(
        f"{message.role}: {message.content[:400]}"
        if isinstance(message, ChatMessage)
        else f"{message.role}: {str(message.parts)[:400]}"
        for message in messages
    )


def _bounded_previews(previews: list[str], max_chars: int) -> list[str]:
    selected: list[str] = []
    remaining = max_chars
    for preview in reversed(previews):
        if remaining <= 0:
            break
        value = preview[:remaining]
        selected.append(value)
        remaining -= len(value)
    selected.reverse()
    return selected


def _covered_message_ids(
    store: SessionStore,
    session_id: str,
    turn_id: str,
    retained_count: int,
    previous: tuple[str, ...],
) -> tuple[str, ...]:
    user_message = store.get_user_message_for_turn(turn_id)
    messages = [
        message
        for message, _ in store.list_messages_with_parts(session_id)
        if message.turn_id == turn_id
        and message.metadata.get("summary_id") is None
        and (user_message is None or message.message_id != user_message.message_id)
    ]
    newly_covered = messages[:-retained_count] if retained_count else messages
    return tuple(dict.fromkeys([*previous, *(message.message_id for message in newly_covered)]))


def _recovered_base_message_count(
    store: SessionStore,
    session_id: str,
    turn_id: str,
    messages: list[ChatMessage | RichChatMessage],
    covered_message_ids: tuple[str, ...],
) -> int:
    user_message = store.get_user_message_for_turn(turn_id)
    recent_count = sum(
        1
        for message, _ in store.list_messages_with_parts(session_id)
        if message.turn_id == turn_id
        and message.status not in {"failed", "in_progress"}
        and message.message_id not in covered_message_ids
        and message.metadata.get("summary_id") is None
        and (user_message is None or message.message_id != user_message.message_id)
    )
    return max(0, len(messages) - recent_count)


def _fit_to_window(
    base: list[ChatMessage | RichChatMessage],
    checkpoint: ChatMessage | None,
    groups: list[list[ChatMessage | RichChatMessage]],
    profile: ModelContextProfile,
) -> list[ChatMessage | RichChatMessage]:
    fixed = [*base, *([checkpoint] if checkpoint is not None else [])]
    limit = profile.max_input_tokens - profile.protocol_overhead_tokens
    used = _tokens(fixed)
    if used >= limit:
        raise ContextBudgetExceeded("turn checkpoint does not fit beside mandatory context", reason="turn_checkpoint_overflow")
    selected: list[list[ChatMessage | RichChatMessage]] = []
    for group in reversed(groups):
        group_tokens = _tokens(group)
        if len(selected) < 3 and used + group_tokens <= limit:
            selected.append(group)
            used += group_tokens
            continue
        if not selected:
            selected.append(_truncate_group(group, max(1, limit - used)))
        break
    selected.reverse()
    result = [*fixed, *(message for group in selected for message in group)]
    if _tokens(result) > limit:
        raise ContextBudgetExceeded("current turn cannot fit in the model input budget", reason="turn_window_overflow")
    return result


def _truncate_group(
    group: list[ChatMessage | RichChatMessage],
    token_budget: int,
) -> list[ChatMessage | RichChatMessage]:
    chat_messages = [message for message in group if isinstance(message, ChatMessage)]
    if not chat_messages:
        raise ContextBudgetExceeded("rich current-turn group exceeds input budget", reason="turn_group_overflow")
    chars_per_message = max(0, token_budget * 4 // len(chat_messages) - 80)
    truncated = [
        ChatMessage(message.role, message.content[:chars_per_message] + "\n... truncated in rolling context")
        for message in chat_messages
    ]
    if _tokens(truncated) > token_budget:
        raise ContextBudgetExceeded("latest current-turn group exceeds input budget", reason="turn_group_overflow")
    return truncated
