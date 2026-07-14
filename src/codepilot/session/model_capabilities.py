from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelContextProfile:
    """模型上下文窗口及 reasoning 重放能力。"""

    provider: str
    model: str
    max_input_tokens: int
    supports_reasoning_replay: bool
    max_output_tokens: int = 4_096
    supports_native_tool_calls: bool = False
    reasoning_format: str | None = None


_KNOWN_CONTEXT_WINDOWS = {
    "gpt-4": 8_192,
    "gpt-4o": 128_000,
    "claude-3": 200_000,
}


def resolve_model_context_profile(provider: str, model: str) -> ModelContextProfile:
    """返回显式有限的默认窗口，避免未知模型被当成无限窗口。"""

    max_input_tokens = next((size for prefix, size in _KNOWN_CONTEXT_WINDOWS.items() if model.startswith(prefix)), 16_384)
    return ModelContextProfile(provider, model, max_input_tokens=max_input_tokens, supports_reasoning_replay=False)
