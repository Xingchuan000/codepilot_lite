from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codepilot.agent.prompts import build_system_prompt
from codepilot.llm.types import ChatMessage, ChatMessagePart, RichChatMessage
from codepilot.memory.rendering import render_session_summary
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.context_budget import ContextBudgetAllocator, ContextItem, ContextPlan, estimate_tokens
from codepilot.session.errors import SessionProtocolMismatch
from codepilot.session.model_context import ModelContextProfile
from codepilot.session.models import ContextSummaryRecord, MessagePartRecord, MessageRecord
from codepilot.session.repositories import SessionRepositories
from codepilot.tools.base import ToolSpec


@dataclass(frozen=True)
class SessionHistory:
    session_id: str
    current_turn_id: str
    project_path: Path
    summaries: tuple[ContextSummaryRecord, ...]
    messages: tuple[tuple[MessageRecord, tuple[MessagePartRecord, ...]], ...]
    branch_events: tuple[ChatMessage, ...] = ()
    instruction_items: tuple[ContextItem, ...] = ()
    memory_items: tuple[ContextItem, ...] = ()
    turn_checkpoint_items: tuple[ContextItem, ...] = ()
    turn_checkpoint_covered_ids: tuple[str, ...] = ()
    inherited_items: tuple[ContextItem, ...] = ()


@dataclass(frozen=True)
class PreparedContext:
    messages: list[ChatMessage | RichChatMessage]
    selected_items: tuple[ContextItem, ...]
    omitted_items: tuple[ContextItem, ...]
    estimated_tokens: int
    metadata: dict[str, object]


class ProviderContextAdapter:
    """把 SQLite 历史恢复为 Provider 可重放的 Native 消息。"""

    def __init__(self, store: SessionRepositories, artifacts: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifacts = artifacts or ArtifactStore(store.database)

    def build_context_plan(
        self,
        history: SessionHistory,
        profile: ModelContextProfile,
        *,
        tool_specs: tuple[ToolSpec, ...] | None = None,
        agent_instructions: str | None = None,
    ) -> ContextPlan:
        """先生成 ContextPlan，再由统一预算器选择整组消息。

        规划阶段按业务优先级先锁定当前 Turn，并从新到旧尝试加入可选历史，保证预算
        不足时优先保留最近事实；最终发送给模型前会恢复为正常的时间顺序。任何一个
        工具调用组都不会在这里拆成独立字符串。
        """

        validate_current_session_protocol(history.messages)

        system_messages = [
            ChatMessage(
                "system",
                build_system_prompt(tool_specs=tool_specs, agent_instructions=agent_instructions),
            )
        ]
        system_messages.extend(history.branch_events)
        system_items = tuple(
            ContextItem(
                key=f"system-{index}",
                messages=(message,),
                estimated_tokens=estimate_tokens(message),
                mandatory=True,
                priority=1000 - index,
                source_kind="system_prompt" if index == 0 else "system_event",
            )
            for index, message in enumerate(system_messages)
        ) + tuple(item for item in history.instruction_items if item.mandatory)

        covered_message_ids: set[str] = set()
        covered_message_ids.update(history.turn_checkpoint_covered_ids)
        summary_items: list[ContextItem] = [
            *(item for item in history.instruction_items if not item.mandatory),
            *history.inherited_items,
            *history.memory_items,
        ]
        for index, summary in enumerate(history.summaries):
            if summary.status != "completed":
                continue
            covered_message_ids.update(str(item) for item in summary.metadata.get("covered_message_ids", []))
            content = _summary_content(summary)
            if content:
                message = ChatMessage("system", content)
                summary_items.append(
                    ContextItem(
                        key=f"summary-{summary.summary_id}",
                        messages=(message,),
                        estimated_tokens=estimate_tokens(message),
                        mandatory=bool(summary.metadata.get("covered_message_ids")),
                        priority=900 - index,
                        source_kind="summary",
                        source_ids=(summary.summary_id,),
                    )
                )
        summary_items.extend(history.turn_checkpoint_items)

        grouped: dict[str, list[ChatMessage | RichChatMessage]] = {}
        group_metadata: dict[str, tuple[bool, int, str | None]] = {}
        call_message_ids = {
            call.message_id: call.tool_call_id
            for call in self.store.tool_executions.list_tool_calls(history.session_id)
            if call.message_id is not None
        }
        for message, parts in history.messages:
            if message.metadata.get("summary_id") is not None or message.message_id in covered_message_ids:
                continue
            if message.status in {"failed", "in_progress"}:
                continue
            rendered = self.render_message_for_context(message, tuple(parts), profile)
            if rendered is None:
                continue
            if message.turn_id == history.current_turn_id and message.role == "user":
                rendered = ChatMessage("user", f"{rendered.content}\nRepository: {history.project_path}")
            tool_call_ids = _tool_call_ids(message, parts)
            if message.message_id in call_message_ids:
                tool_call_ids.add(call_message_ids[message.message_id])
            key = f"tool-{sorted(tool_call_ids)[0]}" if tool_call_ids else f"message-{message.message_id}"
            grouped.setdefault(key, []).append(rendered)
            mandatory = message.turn_id == history.current_turn_id or bool(tool_call_ids & _unresolved_tool_call_ids(self.store, history.session_id))
            priority = 950 if message.turn_id == history.current_turn_id else 500
            atomic_group = key if tool_call_ids else None
            previous = group_metadata.get(key)
            if previous is not None:
                mandatory = mandatory or previous[0]
                priority = max(priority, previous[1])
                atomic_group = previous[2] or atomic_group
            group_metadata[key] = (mandatory, priority, atomic_group)

        current_turn_items: list[ContextItem] = []
        history_items: list[ContextItem] = []
        message_order = {message.message_id: index for index, (message, _) in enumerate(history.messages)}
        for key, messages in grouped.items():
            mandatory, priority, atomic_group = group_metadata[key]
            first_message_id = next(
                message.message_id
                for message, parts in history.messages
                if _context_key(message, parts) == key
            )
            item = ContextItem(
                key=key,
                messages=tuple(messages),
                estimated_tokens=sum(estimate_tokens(message) for message in messages),
                mandatory=mandatory,
                priority=priority,
                atomic_group=atomic_group,
                source_kind="tool_exchange" if key.startswith("tool-") else "message",
                source_ids=(key.removeprefix("tool-"),) if key.startswith("tool-") else (first_message_id,),
            )
            if mandatory:
                current_turn_items.append(item)
            else:
                history_items.append((message_order[first_message_id], item))

        # 可选历史必须从最新到最旧消费，防止旧记录把当前有效事实挤出窗口。
        history_items = [item for _, item in sorted(history_items, key=lambda value: value[0], reverse=True)]
        return ContextPlan(system_items=system_items, summary_items=tuple(summary_items), history_items=tuple(item for item in history_items), current_turn_items=tuple(current_turn_items))

    def build_messages(
        self,
        history: SessionHistory,
        profile: ModelContextProfile,
        *,
        tool_specs: tuple[ToolSpec, ...] | None = None,
        agent_instructions: str | None = None,
    ) -> list[ChatMessage | RichChatMessage]:
        return self.build_prepared_context(
            history,
            profile,
            tool_specs=tool_specs,
            agent_instructions=agent_instructions,
        ).messages

    def build_prepared_context(
        self,
        history: SessionHistory,
        profile: ModelContextProfile,
        *,
        tool_specs: tuple[ToolSpec, ...] | None = None,
        agent_instructions: str | None = None,
    ) -> PreparedContext:
        plan = self.build_context_plan(
            history,
            profile,
            tool_specs=tool_specs,
            agent_instructions=agent_instructions,
        )
        budget = ContextBudgetAllocator(profile.max_input_tokens, protocol_overhead_tokens=profile.protocol_overhead_tokens)
        selected: list[ContextItem] = []
        for item in plan.system_items:
            budget.require(item)
            selected.append(item)
        for item in plan.current_turn_items:
            budget.require(item)
            selected.append(item)
        for item in plan.summary_items + plan.history_items:
            if item.mandatory:
                budget.require(item)
                selected.append(item)
            else:
                budget.try_add(item)
                if item in budget.selected_items():
                    selected.append(item)
        selected_keys = {item.key for item in selected}
        selected_history = [item for item in plan.history_items if item.key in selected_keys]
        # 预算分配必须从新到旧尝试历史，但模型收到的对话必须从旧到新排列；否则
        # 当前问题会出现在历史前面，Assistant/User 轮次也会倒置，模型无法理解“刚刚”。
        selected_history.reverse()
        selected_summaries = [item for item in plan.summary_items if item.key in selected_keys]
        selected_current_turn = [item for item in plan.current_turn_items if item.key in selected_keys]
        ordered = plan.system_items + tuple(selected_summaries) + tuple(selected_history) + tuple(selected_current_turn)
        messages = [message for item in ordered for message in item.messages]
        budget.verify(messages)
        all_items = plan.system_items + plan.summary_items + plan.history_items + plan.current_turn_items
        return PreparedContext(
            messages,
            ordered,
            tuple(item for item in all_items if item.key not in selected_keys),
            sum(estimate_tokens(message) for message in messages) + profile.protocol_overhead_tokens,
            {"max_input_tokens": profile.max_input_tokens, "protocol_overhead_tokens": profile.protocol_overhead_tokens},
        )

    def render_message_for_context(
        self,
        message: MessageRecord,
        parts: tuple[MessagePartRecord, ...],
        profile: ModelContextProfile,
    ) -> ChatMessage | RichChatMessage | None:
        return _render_message(self.artifacts, message, parts, profile)


def _context_key(message: MessageRecord, parts: tuple[MessagePartRecord, ...]) -> str:
    tool_call_ids = _tool_call_ids(message, parts)
    return f"tool-{sorted(tool_call_ids)[0]}" if tool_call_ids else f"message-{message.message_id}"


def _tool_call_ids(message: MessageRecord, parts: tuple[MessagePartRecord, ...]) -> set[str]:
    ids = {str(message.metadata["tool_call_id"])} if message.metadata.get("tool_call_id") is not None else set()
    ids.update(str(part.metadata["tool_call_id"]) for part in parts if part.metadata.get("tool_call_id") is not None)
    ids.update(
        str(part.content["codepilot_tool_call_id"])
        for part in parts
        if isinstance(part.content, dict) and part.content.get("codepilot_tool_call_id") is not None
    )
    return ids


def _unresolved_tool_call_ids(store: SessionRepositories, session_id: str) -> set[str]:
    return {call.tool_call_id for call in store.tool_executions.list_unresolved_tool_calls() if _call_belongs_to_session(store, call.turn_id, session_id)}


def _call_belongs_to_session(store: SessionRepositories, turn_id: str, session_id: str) -> bool:
    return store.turns.get_turn(turn_id).session_id == session_id


def _summary_content(summary: ContextSummaryRecord) -> str:
    content = (
        summary.content
        if isinstance(summary.content, str)
        else render_session_summary(summary.content)
    )
    return f"Persisted context summary ({summary.model}):\n{content}" if summary.model else f"Persisted context summary:\n{content}"


def _render_message(
    artifacts: ArtifactStore,
    message: MessageRecord,
    parts: tuple[MessagePartRecord, ...],
    profile: ModelContextProfile,
) -> ChatMessage | RichChatMessage | None:
    if message.role == "assistant":
        if not parts:
            raise SessionProtocolMismatch("session uses unsupported pre-native-message format")
        rendered_parts: list[ChatMessagePart] = []
        for part in parts:
            if not part.replayable:
                continue
            if part.type == "text":
                content = _part_content(artifacts, part)
                if content:
                    rendered_parts.append(ChatMessagePart(type="text", content=content))
                continue
            if part.type == "reasoning":
                if not profile.supports_reasoning_replay or part.provider_format not in {None, profile.reasoning_format, profile.provider}:
                    continue
                content = _part_content(artifacts, part)
                if content:
                    rendered_parts.append(ChatMessagePart(type="text", content=content))
                continue
            if part.type == "reasoning_replay":
                if not isinstance(part.content, dict):
                    raise ValueError("reasoning_replay content must be a dict")
                rendered_parts.append(
                    ChatMessagePart(
                        type="reasoning_replay",
                        content=part.content,
                        provider_format=part.provider_format,
                        replayable=True,
                    )
                )
                continue
            if part.type != "tool_call":
                raise ValueError(f"Unsupported assistant message part: {part.type}")
            if not isinstance(part.content, dict):
                raise ValueError("tool_call part content must be a dict")
            data = part.content
            if not isinstance(data.get("provider_tool_call_id"), str) or not data["provider_tool_call_id"]:
                raise SessionProtocolMismatch(
                    "Session tool-call record is missing provider_tool_call_id; start a new session"
                )
            rendered_parts.append(
                ChatMessagePart(
                    type="tool_call",
                    content={
                        "provider_tool_call_id": data["provider_tool_call_id"],
                        "tool_name": data["tool_name"],
                        "arguments": data["arguments"],
                    },
                )
            )
        if rendered_parts:
            if message.status == "interrupted":
                rendered_parts.append(
                    ChatMessagePart(
                        type="text",
                        content="The previous assistant response was interrupted. Use the persisted content only as evidence and produce a complete response again. Do not continue from the last character.",
                    )
                )
            return RichChatMessage(role="assistant", parts=tuple(rendered_parts))
        content = _message_content(artifacts, parts, profile, message.status)
        return ChatMessage(role="assistant", content=content) if content else None

    if message.role == "tool":
        tool_parts = [part for part in parts if part.replayable and part.type == "tool_result"]
        if len(tool_parts) != 1:
            raise SessionProtocolMismatch(
                "Session tool-result record is missing its tool part; start a new session"
            )
        if not isinstance(tool_parts[0].content, dict):
            raise ValueError("tool_result content must be a dict")
        data = tool_parts[0].content
        if not isinstance(data.get("provider_tool_call_id"), str) or not data["provider_tool_call_id"]:
            raise SessionProtocolMismatch(
                "Session tool-result record is missing provider_tool_call_id; start a new session"
            )
        return RichChatMessage(
            role="tool",
            parts=(ChatMessagePart(type="tool_result", content=data),),
        )

    content = _message_text(message.content)
    if not content:
        return None
    return ChatMessage(role=message.role, content=content)


def _message_content(artifacts: ArtifactStore, parts: tuple[MessagePartRecord, ...], profile: ModelContextProfile, status: str) -> str:
    values: list[str] = []
    for part in parts:
        if not part.replayable:
            continue
        if part.type == "tool_call":
            # Native tool calls are replayed as structured assistant parts above.
            continue
        if part.type == "reasoning" and (
            not profile.supports_reasoning_replay
            or part.provider_format not in {None, profile.reasoning_format, profile.provider}
        ):
            continue
        text = _part_content(artifacts, part)
        if text:
            values.append(text)
    content = "\n".join(values)
    if status == "interrupted" and not content:
        return ""
    if status == "interrupted":
        content += "\nThe previous assistant response was interrupted. Use the persisted content only as evidence and produce a complete response again. Do not continue from the last character."
    return content


def validate_current_session_protocol(
    messages_with_parts: Iterable[tuple[MessageRecord, tuple[MessagePartRecord, ...]]],
) -> None:
    for message, parts in messages_with_parts:
        if message.role in {"assistant", "tool"} and not parts:
            raise SessionProtocolMismatch("session uses unsupported pre-native-message format")


def _part_content(artifacts: ArtifactStore, part: MessagePartRecord) -> str:
    # 大 Artifact 的安全预览已经在 Content Persistence 层写入 Part.content；这里不再
    # 按剩余预算截断，避免产生无法解析的 Tool JSON 或丢失当前 User Message。
    if part.content not in (None, ""):
        return _message_text(part.content)
    if part.artifact_id is not None:
        return artifacts.read_text(part.artifact_id)
    return ""


def _message_text(content: object) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
