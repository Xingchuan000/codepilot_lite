from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapabilities:
    """真实 Provider/Adapter 对当前模型声明的能力。"""

    provider: str
    model: str
    max_context_tokens: int
    max_output_tokens: int
    reasoning_format: str | None = None
    supports_reasoning_replay: bool = False
    source: str = "registry"


@dataclass(frozen=True)
class ModelContextProfile:
    """一次 Turn 使用的不可变上下文能力快照。"""

    provider: str
    model: str
    max_input_tokens: int
    supports_reasoning_replay: bool
    max_output_tokens: int = 4_096
    supports_native_tool_calls: bool = False
    reasoning_format: str | None = None
    capability_source: str = "registry"
    protocol_overhead_tokens: int = 64


# 未知模型使用小窗口的保守值；只把项目明确知道的前缀写入 registry，避免误称为大窗口。
_KNOWN_CONTEXT_WINDOWS = {
    "gpt-4o": 128_000,
    "gpt-4.1": 128_000,
    "gpt-5": 128_000,
    "claude-3": 200_000,
    "claude-sonnet-4": 200_000,
    "gemini-": 1_000_000,
}


def resolve_model_capabilities(provider: str, model: str) -> ModelCapabilities:
    normalized = model.split("/", 1)[-1].lower()
    prefix, size = next(
        ((prefix, size) for prefix, size in sorted(_KNOWN_CONTEXT_WINDOWS.items(), key=lambda item: len(item[0]), reverse=True) if normalized.startswith(prefix)),
        ("unknown", 16_384),
    )
    reasoning_format = "openai_reasoning" if provider == "openai" and normalized.startswith(("o1", "o3", "o4", "gpt-5")) else None
    return ModelCapabilities(
        provider=provider,
        model=model,
        max_context_tokens=size,
        max_output_tokens=min(16_384, max(4_096, size // 8)),
        reasoning_format=reasoning_format,
        supports_reasoning_replay=reasoning_format is not None,
        source="registry" if prefix != "unknown" else "conservative_unknown_model",
    )


def resolve_model_context_profile(
    provider: str,
    model: str,
    capabilities: ModelCapabilities | None = None,
) -> ModelContextProfile:
    """优先使用 Adapter 能力，其次使用保守 registry，并固定到 Turn Snapshot。"""

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
