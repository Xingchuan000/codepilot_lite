from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
class LLMResponse:
    """CodePilot 最小模型响应结构。"""

    content: str
    raw: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class CodePilotLLMClient(Protocol):
    """MinimalAgentLoop 依赖的最小 LLM 协议。"""

    def complete(self, messages: list[ChatMessage]) -> LLMResponse:
        ...
