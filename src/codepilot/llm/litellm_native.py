from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import litellm

from codepilot.llm.errors import normalize_llm_exception
from codepilot.llm.tool_schema import build_provider_tool_name_map, to_litellm_tools
from codepilot.llm.types import (
    ChatMessage,
    CodePilotLLMClient,
    LLMReasoningReplay,
    LLMResponse,
    LLMToolCall,
    RichChatMessage,
)
from codepilot.llm.provider_messages import to_provider_messages
from codepilot.tools.base import ToolSpec


class LLMProtocolError(RuntimeError):
    pass


def _parse_tool_call(
    call: Any,
    *,
    provider_to_codepilot_name: Mapping[str, str] | None = None,
) -> LLMToolCall:
    provider_id = call.id
    provider_name = call.function.name
    raw_arguments = call.function.arguments

    if not provider_id:
        raise LLMProtocolError("LiteLLM returned a tool call without id")
    if not provider_name:
        raise LLMProtocolError("LiteLLM returned a tool call without function name")
    if not isinstance(raw_arguments, str):
        raise LLMProtocolError("LiteLLM tool arguments must be a JSON string")

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise LLMProtocolError("LiteLLM returned invalid JSON tool arguments") from exc
    if not isinstance(arguments, dict):
        raise LLMProtocolError("Tool arguments must decode to a JSON object")

    name = (provider_to_codepilot_name or {}).get(provider_name, provider_name)
    return LLMToolCall(provider_tool_call_id=provider_id, name=name, arguments=arguments)


def _reasoning_replay_from_message(message: Any) -> LLMReasoningReplay | None:
    blocks = getattr(message, "thinking_blocks", None)
    if not blocks:
        return None

    normalized: list[dict[str, Any]] = []
    for block in blocks:
        value = block.model_dump(mode="json") if hasattr(block, "model_dump") else dict(block)
        normalized.append(value)
    return LLMReasoningReplay(
        provider_format="anthropic_thinking_blocks",
        blocks=tuple(normalized),
    )


@dataclass
class LiteLLMNativeClient(CodePilotLLMClient):
    model_name: str
    model_kwargs: dict[str, Any]

    def complete(
        self,
        messages: list[ChatMessage | RichChatMessage],
        *,
        tools: list[ToolSpec] | tuple[ToolSpec, ...] = (),
        tool_choice: str = "auto",
    ) -> LLMResponse:
        tool_name_map = build_provider_tool_name_map(tools)
        provider_to_codepilot_name = {provider: internal for internal, provider in tool_name_map.items()}
        request: dict[str, Any] = {
            "model": self.model_name,
            "messages": to_provider_messages(messages, tool_name_map=tool_name_map),
            "drop_params": False,
            **self.model_kwargs,
        }
        if tools:
            request["tools"] = to_litellm_tools(tools, tool_name_map=tool_name_map)
            # Provider defaults already mean "auto" when tools are present.  Omitting
            # the explicit field avoids requiring a separate tool_choice capability.
            if tool_choice != "auto":
                request["tool_choice"] = tool_choice

        try:
            response = litellm.completion(**request)
        except Exception as exc:
            raise normalize_llm_exception(exc, output_started=False) from exc

        choice = response.choices[0]
        message = choice.message
        tool_calls = tuple(
            _parse_tool_call(call, provider_to_codepilot_name=provider_to_codepilot_name)
            for call in (message.tool_calls or ())
        )
        usage = response.usage.model_dump(mode="json") if response.usage else {}
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            reasoning_replay=_reasoning_replay_from_message(message),
            model=response.model or self.model_name,
            usage=usage,
            finish_reason=choice.finish_reason,
            raw={},
        )
