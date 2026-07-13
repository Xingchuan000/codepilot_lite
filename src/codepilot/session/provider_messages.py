from __future__ import annotations

from typing import Any

from codepilot.llm.types import ChatMessage, ChatMessagePart, RichChatMessage


def to_provider_messages(messages: list[RichChatMessage | ChatMessage]) -> list[dict[str, Any]]:
    """把统一消息结构转换成大多数 Provider 接受的字典格式。"""

    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ChatMessage):
            result.append({"role": message.role, "content": message.content})
            continue
        result.append(
            {
                "role": message.role,
                "content": [
                    {"type": part.type, "content": part.content, **({"provider_format": part.provider_format} if part.provider_format else {})}
                    for part in message.parts
                    if part.replayable
                ],
            }
        )
    return result
