from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codepilot.agent.prompts import build_system_prompt
from codepilot.llm.types import ChatMessage, RichChatMessage
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.context_budget import ContextBudgetAllocator
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


class TextActionContextAdapter:
    """把 SQLite 历史回放成当前 loop 能直接消费的文本上下文。"""

    def __init__(self, store: SessionStore, artifacts: ArtifactStore | None = None) -> None:
        self.store = store
        self.artifacts = artifacts or ArtifactStore(store.database)

    def build_messages(self, history: SessionHistory, profile: ModelContextProfile) -> list[ChatMessage | RichChatMessage]:
        budget = ContextBudgetAllocator(profile.max_input_tokens)
        messages: list[ChatMessage | RichChatMessage] = [ChatMessage("system", budget.reserve_system(build_system_prompt()))]
        covered_message_ids: set[str] = set()
        for summary in history.summaries:
            if summary.status != "completed":
                continue
            covered_message_ids.update(str(item) for item in summary.metadata.get("covered_message_ids", []))
            content = _summary_content(summary)
            if content:
                messages.append(ChatMessage("system", budget.consume_summary(content)))
        for message, parts in history.messages:
            if message.metadata.get("summary_id") is not None:
                # 摘要索引是唯一注入入口，Summary Message 仅保留为审计实体。
                continue
            if message.message_id in covered_message_ids:
                continue
            if message.status in {"failed", "in_progress"}:
                continue
            content = _message_content(self.artifacts, message, parts, budget.remaining_chars(), profile)
            content = budget.consume_message(content)
            if not content:
                continue
            if message.status == "interrupted" and message.role == "assistant":
                content += "\nThe previous assistant response was interrupted. Use the persisted content only as evidence and produce a complete response again. Do not continue from the last character."
            if message.turn_id == history.current_turn_id and message.role == "user":
                content += f"\nRepository: {history.project_path}"
            if message.role == "assistant":
                messages.append(ChatMessage("assistant", content))
            elif message.role == "tool":
                messages.append(ChatMessage("user", content))
            else:
                messages.append(ChatMessage(message.role, content))
        return messages


def _summary_content(summary: ContextSummaryRecord) -> str:
    content = summary.content if isinstance(summary.content, str) else json.dumps(summary.content, ensure_ascii=False)
    if summary.model:
        return f"Persisted context summary ({summary.model}):\n{content}"
    return f"Persisted context summary:\n{content}"


def _message_content(
    artifacts: ArtifactStore,
    message: MessageRecord,
    parts: tuple[MessagePartRecord, ...],
    budget_chars: int,
    profile: ModelContextProfile,
) -> str:
    if parts:
        values: list[str] = []
        remaining = budget_chars
        for part in parts:
            if not part.replayable:
                continue
            if part.type == "reasoning" and (
                not profile.supports_reasoning_replay
                or not _provider_format_compatible(part.provider_format, profile)
            ):
                continue
            if part.type == "tool_call":
                # Text Action 的原始 JSON 已在 text Part 中保存；结构化 Part
                # 只供 Native Tool Provider 消费，不能再次拼入文本正文。
                continue
            text = _part_content(artifacts, part, remaining)
            if not text:
                continue
            remaining = max(0, remaining - len(text))
            values.append(text)
        # 存在结构化 Part 时，过滤结果为空也不能回退到聚合 content，否则 reasoning
        # 会绕过能力检查重新泄漏给不兼容模型。
        return "\n".join(values)
    return _message_text(message.content)


def _part_content(artifacts: ArtifactStore, part: MessagePartRecord, budget_chars: int) -> str:
    if part.type == "tool_result":
        # ToolResult Artifact 保存原始业务输出，而 Part 正文保存规范 observation。回放时
        # 必须采用后者，才能与当前 Attempt 实际交给模型的文本完全一致。
        return _message_text(part.content)
    if part.artifact_id is not None and budget_chars > 0:
        text = artifacts.read_text(part.artifact_id)
        if len(text) <= budget_chars:
            return text
    return _message_text(part.content)


def _provider_format_compatible(provider_format: str | None, profile: ModelContextProfile) -> bool:
    """仅重放无专属格式或明确属于当前 Provider 的 reasoning。"""

    return provider_format is None or provider_format == profile.provider


def _message_text(content: object) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
