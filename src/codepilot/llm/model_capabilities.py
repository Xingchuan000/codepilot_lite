from __future__ import annotations

import logging
from typing import Any

import litellm

from codepilot.llm.capabilities import ModelCapabilities

logger = logging.getLogger(__name__)


def resolve_litellm_model_capabilities(
    *,
    provider: str,
    model: str,
) -> ModelCapabilities | None:
    try:
        info: dict[str, Any] = litellm.get_model_info(model=model)
    except Exception as exc:
        if "isn't mapped yet" not in str(exc):
            raise
        logger.warning(
            "LiteLLM has no model capability metadata",
            extra={"provider": provider, "model": model, "error_type": type(exc).__name__},
        )
        return None

    max_input = info.get("max_input_tokens")
    max_output = info.get("max_output_tokens")
    if not isinstance(max_input, int) or max_input <= 0:
        return None
    if not isinstance(max_output, int) or max_output <= 0:
        max_output = min(16_384, max(4_096, max_input // 8))

    return ModelCapabilities(
        provider=provider,
        model=model,
        max_context_tokens=max_input,
        max_output_tokens=max_output,
        reasoning_format=None,
        supports_reasoning_replay=False,
        source="litellm_model_info",
    )
