from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.llm.types import (
    ChatMessage,
    ChatMessagePart,
    CodePilotLLMClient,
    LLMReasoningReplay,
    LLMResponse,
    LLMStreamEvent,
    LLMToolCall,
    RichChatMessage,
)

if TYPE_CHECKING:
    from codepilot.llm.fake import StructuredFakeLLM as StructuredFakeLLM
    from codepilot.llm.litellm_native import LiteLLMNativeClient as LiteLLMNativeClient

__all__ = [
    "ChatMessage",
    "ChatMessagePart",
    "RichChatMessage",
    "CodePilotLLMClient",
    "StructuredFakeLLM",
    "LLMResponse",
    "LLMReasoningReplay",
    "LLMStreamEvent",
    "LLMToolCall",
    "LiteLLMNativeClient",
]


def __getattr__(name: str):
    if name == "StructuredFakeLLM":
        from codepilot.llm.fake import StructuredFakeLLM

        return StructuredFakeLLM
    if name == "LiteLLMNativeClient":
        from codepilot.llm.litellm_native import LiteLLMNativeClient

        return LiteLLMNativeClient
    raise AttributeError(name)
