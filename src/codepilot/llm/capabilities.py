from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapabilities:
    """Provider/model 宣布的能力，归 LLM domain 所有。"""

    provider: str
    model: str
    max_context_tokens: int
    max_output_tokens: int
    reasoning_format: str | None = None
    supports_reasoning_replay: bool = False
    source: str = "registry"


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
        (
            (prefix, size)
            for prefix, size in sorted(_KNOWN_CONTEXT_WINDOWS.items(), key=lambda item: len(item[0]), reverse=True)
            if normalized.startswith(prefix)
        ),
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
