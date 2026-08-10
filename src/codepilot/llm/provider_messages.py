from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from codepilot.llm.tool_schema import to_provider_tool_name
from codepilot.llm.types import ChatMessage, RichChatMessage


def to_provider_messages(
    messages: list[RichChatMessage | ChatMessage],
    *,
    tool_name_map: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Serialize CodePilot messages using provider-safe native tool names."""

    def provider_tool_name(name: str) -> str:
        return (tool_name_map or {}).get(name, to_provider_tool_name(name))

    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ChatMessage):
            result.append({"role": message.role, "content": message.content})
            continue
        if message.role == "assistant":
            text = ""
            tool_calls: list[dict[str, Any]] = []
            thinking_blocks: list[dict[str, Any]] | None = None
            for part in message.parts:
                if not part.replayable:
                    continue
                if part.type == "text":
                    text += str(part.content)
                elif part.type == "reasoning_replay":
                    if part.provider_format != "anthropic_thinking_blocks" or not isinstance(part.content, dict):
                        raise ValueError("unsupported reasoning replay format")
                    blocks = part.content["blocks"]
                    if not isinstance(blocks, list):
                        raise ValueError("reasoning_replay blocks must be a list")
                    thinking_blocks = blocks
                elif part.type == "tool_call":
                    if not isinstance(part.content, dict):
                        raise ValueError("tool_call content must be a dict")
                    data = part.content
                    tool_calls.append(
                        {
                            "id": data["provider_tool_call_id"],
                            "type": "function",
                            "function": {
                                "name": provider_tool_name(data["tool_name"]),
                                "arguments": json.dumps(data["arguments"], ensure_ascii=False),
                            },
                        }
                    )
                else:
                    raise ValueError(f"Unsupported assistant message part: {part.type}")
            provider_message: dict[str, Any] = {"role": "assistant", "content": text}
            if thinking_blocks:
                provider_message["thinking_blocks"] = thinking_blocks
            if tool_calls:
                provider_message["tool_calls"] = tool_calls
            result.append(provider_message)
            continue
        if message.role == "tool":
            if len(message.parts) != 1:
                raise ValueError("tool message must contain exactly one tool_result")
            part = message.parts[0]
            if part.type != "tool_result" or not isinstance(part.content, dict):
                raise ValueError("tool message must contain one tool_result dict")
            data = part.content
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": data["provider_tool_call_id"],
                    "name": provider_tool_name(data["tool_name"]),
                    "content": str(data["content"]),
                }
            )
            continue
        raise ValueError(f"Unsupported rich message role: {message.role}")
    return result
