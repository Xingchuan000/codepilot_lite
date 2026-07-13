from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelContextProfile:
    """模型上下文窗口及 reasoning 重放能力。"""

    provider: str
    model: str
    max_input_tokens: int
    supports_reasoning_replay: bool


def resolve_model_context_profile(provider: str, model: str) -> ModelContextProfile:
    """返回显式有限的默认窗口，避免未知模型被当成无限窗口。"""

    return ModelContextProfile(provider, model, max_input_tokens=128_000, supports_reasoning_replay=False)
