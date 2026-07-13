from __future__ import annotations

from typing import TYPE_CHECKING

from codepilot.llm.types import ChatMessage, ChatMessagePart, RichChatMessage, CodePilotLLMClient, LLMResponse

if TYPE_CHECKING:
    from codepilot.llm.fake import FakeLLMClient as FakeLLMClient
    from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter as SweAgentModelAdapter

__all__ = [
    "ChatMessage",
    "ChatMessagePart",
    "RichChatMessage",
    "CodePilotLLMClient",
    "FakeLLMClient",
    "LLMResponse",
    "SweAgentModelAdapter",
]


def __getattr__(name: str):
    if name == "FakeLLMClient":
        from codepilot.llm.fake import FakeLLMClient

        return FakeLLMClient
    if name == "SweAgentModelAdapter":
        from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter

        return SweAgentModelAdapter
    raise AttributeError(name)
