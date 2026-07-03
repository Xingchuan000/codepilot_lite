from __future__ import annotations

import json
from pathlib import Path

from codepilot.llm.types import ChatMessage, LLMResponse


class FakeLLMExhaustedError(RuntimeError):
    """Fake LLM 响应被消费完时抛出的异常。"""


class FakeLLMClient:
    """按固定脚本顺序返回响应的最小假模型。

    这个类只做计划要求的几件事：
    1. 记录每次调用时收到的 messages。
    2. 依次返回预先给定的文本。
    3. 响应耗尽时抛出明确异常，方便 loop 测试覆盖。
    """

    def __init__(self, responses: list[str], *, model: str = "fake") -> None:
        self.responses = responses
        self.model = model
        self.index = 0
        self.calls: list[list[ChatMessage]] = []

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "FakeLLMClient":
        """从 JSONL 文件加载假响应。

        每一行既可以是原始文本，也可以是 JSON object。
        如果 object 中带有字符串字段 content，则提取 content；
        否则保留整行原样，这样可以直接把 action JSON object 当响应文本喂给 loop。
        """

        responses: list[str] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                responses.append(line)
                continue
            if isinstance(data, dict) and isinstance(data.get("content"), str):
                responses.append(data["content"])
                continue
            responses.append(line)
        return cls(responses)

    def complete(self, messages: list[ChatMessage]) -> LLMResponse:
        """返回下一个固定响应，并记录这次调用的消息快照。"""

        self.calls.append(list(messages))
        if self.index >= len(self.responses):
            raise FakeLLMExhaustedError("FakeLLMClient responses exhausted")
        current_index = self.index
        self.index += 1
        return LLMResponse(
            content=self.responses[current_index],
            raw={"index": current_index, "model": self.model},
            model=self.model,
        )
