from __future__ import annotations

from dataclasses import dataclass

from codepilot.llm.capabilities import ModelCapabilities, resolve_model_capabilities


@dataclass(frozen=True)
class ModelContextProfile:
    """一次 Turn 使用的不可变上下文能力快照。"""

    provider: str
    model: str
    max_input_tokens: int
    supports_reasoning_replay: bool
    max_output_tokens: int = 4_096
    reasoning_format: str | None = None
    capability_source: str = "registry"
    protocol_overhead_tokens: int = 64


def resolve_model_context_profile(
    provider: str,
    model: str,
    capabilities: ModelCapabilities | None = None,
) -> ModelContextProfile:
    """把 provider 能力固定为当前 Turn 的上下文快照。"""

    capabilities = capabilities or resolve_model_capabilities(provider, model)
    if capabilities.provider != provider or capabilities.model != model:
        raise ValueError("model capabilities do not match the requested provider/model")
    return ModelContextProfile(
        provider=provider,
        model=model,
        max_input_tokens=capabilities.max_context_tokens,
        supports_reasoning_replay=capabilities.supports_reasoning_replay,
        max_output_tokens=capabilities.max_output_tokens,
        reasoning_format=capabilities.reasoning_format,
        capability_source=capabilities.source,
    )
