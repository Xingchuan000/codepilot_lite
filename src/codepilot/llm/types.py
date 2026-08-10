from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from codepilot.tools.base import ToolSpec


@dataclass(frozen=True)
class ChatMessage:
    """CodePilot 最小消息结构。

    这里故意不在类型层强限制 role 的取值，
    因为计划要求由调用方自行约束 system/user/assistant。
    """

    role: str
    content: str


@dataclass(frozen=True)
class ChatMessagePart:
    """Provider 无关的消息分片；replayable 控制是否可以重放给模型。"""

    type: str
    content: str | dict[str, Any]
    provider_format: str | None = None
    replayable: bool = True


@dataclass(frozen=True)
class RichChatMessage:
    """允许一条消息同时包含文本、工具调用和工具结果。"""

    role: str
    parts: tuple[ChatMessagePart, ...]


@dataclass(frozen=True)
class LLMToolCall:
    provider_tool_call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMReasoningReplay:
    provider_format: str
    blocks: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class LLMResponse:
    """CodePilot 最小模型响应结构。"""

    content: str
    tool_calls: tuple[LLMToolCall, ...] = ()
    reasoning_replay: LLMReasoningReplay | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None


@dataclass(frozen=True)
class LLMStreamEvent:
    """流式模型事件；只保存 Provider 实际返回的内容。"""

    type: str
    content: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    provider_format: str | None = None
    replayable: bool = True


class CodePilotLLMClient(Protocol):
    """MinimalAgentLoop 依赖的最小 LLM 协议。"""

    def complete(
        self,
        messages: list[ChatMessage | RichChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: str = "auto",
    ) -> LLMResponse:
        ...
