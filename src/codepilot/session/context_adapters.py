from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codepilot.agent.prompts import build_system_prompt
from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.context_budget import ContextBudgetAllocator, ContextItem, ContextPlan, estimate_tokens
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.models import ContextSummaryRecord, MessagePartRecord, MessageRecord
from codepilot.session.store import SessionStore


class ContextMessageAdapter(Protocol):
    def build_messages(self, history: "SessionHistory", profile: ModelContextProfile) -> list[ChatMessage | RichChatMessage]: ...


@dataclass(frozen=True)
class SessionHistory:
    session_id: str
    current_turn_id: str
    project_path: Path
    summaries: tuple[ContextSummaryRecord, ...]
    messages: tuple[tuple[MessageRecord, tuple[MessagePartRecord, ...]], ...]
    branch_events: tuple[ChatMessage, ...] = ()


class TextActionContextAdapter:
    """把 SQLite 历史按完整消息组装成文本上下文。"""

    def __init__(self, store: SessionStore, artifacts: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifacts = artifacts or ArtifactStore(store.database)

    def build_context_plan(self, history: SessionHistory, profile: ModelContextProfile) -> ContextPlan:
        """先生成 ContextPlan，再由统一预算器选择整组消息。

        规划阶段按业务优先级先锁定当前 Turn，并从新到旧尝试加入可选历史，保证预算
        不足时优先保留最近事实；最终发送给模型前会恢复为正常的时间顺序。任何一个
        工具调用组都不会在这里拆成独立字符串。
        """

        system_messages = [ChatMessage("system", build_system_prompt())]
        system_messages.extend(history.branch_events)
        system_items = tuple(
            ContextItem(
                key=f"system-{index}",
                messages=(message,),
                estimated_tokens=estimate_tokens(message),
                mandatory=True,
                priority=1000 - index,
            )
            for index, message in enumerate(system_messages)
        )

        covered_message_ids: set[str] = set()
        summary_items: list[ContextItem] = []
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
                        mandatory=False,
                        priority=800 - index,
                    )
                )

        grouped: dict[str, list[ChatMessage]] = {}
        group_metadata: dict[str, tuple[bool, int, str | None]] = {}
        call_message_ids = {
            call.message_id: call.tool_call_id
            for call in self.store.list_tool_calls(history.session_id)
            if call.message_id is not None
        }
        for message, parts in history.messages:
            if message.metadata.get("summary_id") is not None or message.message_id in covered_message_ids:
                continue
            if message.status in {"failed", "in_progress"}:
                continue
            content = _message_content(self.artifacts, message, parts, profile)
            if not content:
                continue
            if message.turn_id == history.current_turn_id and message.role == "user":
                content += f"\nRepository: {history.project_path}"
            rendered = ChatMessage(_context_role(message.role), content)
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
            item = ContextItem(
                key=key,
                messages=tuple(messages),
                estimated_tokens=sum(estimate_tokens(message) for message in messages),
                mandatory=mandatory,
                priority=priority,
                atomic_group=atomic_group,
            )
            first_message_id = next(
                message.message_id
                for message, parts in history.messages
                if _context_key(message, parts) == key
            )
            if mandatory:
                current_turn_items.append(item)
            else:
                history_items.append((message_order[first_message_id], item))

        # 可选历史必须从最新到最旧消费，防止旧记录把当前有效事实挤出窗口。
        history_items = [item for _, item in sorted(history_items, key=lambda value: value[0], reverse=True)]
        return ContextPlan(system_items=system_items, summary_items=tuple(summary_items), history_items=tuple(item for item in history_items), current_turn_items=tuple(current_turn_items))

    def build_messages(self, history: SessionHistory, profile: ModelContextProfile) -> list[ChatMessage | RichChatMessage]:
        plan = self.build_context_plan(history, profile)
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
        return messages


def _context_role(role: str) -> str:
    # Text Action Adapter 只能使用 Provider 无关的 system/user/assistant 三种角色；Tool
    # Observation 仍作为 user 消息回放，和历史主链已有的调用约定保持一致。
    return "user" if role == "tool" else role


def _context_key(message: MessageRecord, parts: tuple[MessagePartRecord, ...]) -> str:
    tool_call_ids = _tool_call_ids(message, parts)
    return f"tool-{sorted(tool_call_ids)[0]}" if tool_call_ids else f"message-{message.message_id}"


def _tool_call_ids(message: MessageRecord, parts: tuple[MessagePartRecord, ...]) -> set[str]:
    ids = {str(message.metadata["tool_call_id"])} if message.metadata.get("tool_call_id") is not None else set()
    ids.update(str(part.metadata["tool_call_id"]) for part in parts if part.metadata.get("tool_call_id") is not None)
    return ids


def _unresolved_tool_call_ids(store: SessionStore, session_id: str) -> set[str]:
    return {call.tool_call_id for call in store.list_unresolved_tool_calls() if _call_belongs_to_session(store, call.turn_id, session_id)}


def _call_belongs_to_session(store: SessionStore, turn_id: str, session_id: str) -> bool:
    return store.get_turn(turn_id).session_id == session_id


def _summary_content(summary: ContextSummaryRecord) -> str:
    content = summary.content if isinstance(summary.content, str) else json.dumps(summary.content, ensure_ascii=False)
    return f"Persisted context summary ({summary.model}):\n{content}" if summary.model else f"Persisted context summary:\n{content}"


def _message_content(artifacts: ArtifactStore, message: MessageRecord, parts: tuple[MessagePartRecord, ...], profile: ModelContextProfile) -> str:
    if parts:
        values: list[str] = []
        for part in parts:
            if not part.replayable:
                continue
            if part.type == "tool_call":
                # Text Action 的原始 JSON 已由 text Part 持久化；结构化 Part 不重复回放。
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
    else:
        content = _message_text(message.content)
    if message.status == "interrupted" and message.role == "assistant":
        content += "\nThe previous assistant response was interrupted. Use the persisted content only as evidence and produce a complete response again. Do not continue from the last character."
    return content


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
