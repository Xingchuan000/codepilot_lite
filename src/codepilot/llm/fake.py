from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codepilot.llm.types import ChatMessage, LLMResponse, LLMToolCall, RichChatMessage
from codepilot.tools.base import ToolSpec


class FakeLLMExhaustedError(RuntimeError):
    """Fake LLM 响应被消费完时抛出的异常。"""


class StructuredFakeLLM:
    """按固定顺序返回结构化 LLMResponse。"""

    def __init__(self, responses: list[LLMResponse], *, model: str = "fake") -> None:
        self.responses = responses
        self.model = model
        self.index = 0
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_jsonl(cls, path: str | Path) -> StructuredFakeLLM:
        responses: list[LLMResponse] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict) or not isinstance(data.get("content"), str) or not isinstance(data.get("tool_calls"), list):
                raise ValueError("StructuredFakeLLM JSONL requires content and tool_calls fields")
            responses.append(
                LLMResponse(
                    content=data["content"],
                    tool_calls=tuple(
                        LLMToolCall(
                            provider_tool_call_id=call["provider_tool_call_id"],
                            name=call["name"],
                            arguments=call["arguments"],
                        )
                        for call in data["tool_calls"]
                    ),
                    model=data.get("model") if isinstance(data.get("model"), str) else None,
                    usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
                    finish_reason=data.get("finish_reason") if isinstance(data.get("finish_reason"), str) else None,
                )
            )
        return cls(responses)

    def complete(
        self,
        messages: list[ChatMessage | RichChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: str = "auto",
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "tools": tuple(tools), "tool_choice": tool_choice})
        if self.index >= len(self.responses):
            raise FakeLLMExhaustedError("StructuredFakeLLM responses exhausted")
        response = self.responses[self.index]
        self.index += 1
        return response
