from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from codepilot.llm.litellm_native import LiteLLMNativeClient, LLMProtocolError
from codepilot.llm.tool_schema import to_litellm_tools
from codepilot.llm.types import ChatMessage
from codepilot.tools.base import DefaultPermission, ToolRisk, ToolSideEffect, ToolSpec
from codepilot.tools.registry import get_tool_spec


def _response(*, content: str | None, tool_calls: list[object] | None, model: str = "model") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        model=model,
        usage=None,
    )


def test_native_client_sends_input_schema_and_normalizes_tool_calls(monkeypatch) -> None:
    calls = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="working",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}'),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        model="openai/gpt-4o-mini",
        usage=SimpleNamespace(model_dump=lambda mode: {"total_tokens": 3}),
    )

    def completion(**kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", completion)
    result = LiteLLMNativeClient("openai/gpt-4o-mini", {}).complete(
        [ChatMessage(role="user", content="read it")],
        tools=[get_tool_spec("read_file")],
    )

    assert calls[0]["tools"] == to_litellm_tools([get_tool_spec("read_file")])
    assert calls[0]["tools"][0]["type"] == "function"
    assert calls[0]["tools"][0]["function"]["parameters"] == get_tool_spec("read_file").input_schema
    assert "tool_choice" not in calls[0]
    assert calls[0]["drop_params"] is False
    assert result.content == "working"
    assert result.tool_calls[0].provider_tool_call_id == "call-1"
    assert result.tool_calls[0].arguments == {"path": "README.md"}
    assert result.finish_reason == "tool_calls"
    assert result.raw == {}


def test_native_client_rejects_non_json_tool_arguments(monkeypatch) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(name="read_file", arguments="not-json"),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        model="model",
        usage=None,
    )
    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", lambda **kwargs: response)

    with pytest.raises(LLMProtocolError, match="invalid JSON"):
        LiteLLMNativeClient("model", {}).complete([], tools=[])


def test_native_client_returns_plain_text_without_tool_calls(monkeypatch) -> None:
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(content="hello", tool_calls=None)

    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", completion)

    result = LiteLLMNativeClient("model", {}).complete([ChatMessage(role="user", content="hi")])

    assert result.content == "hello"
    assert result.tool_calls == ()
    assert "tools" not in calls[0]
    assert "tool_choice" not in calls[0]


def test_native_client_normalizes_multiple_tool_calls_in_provider_order(monkeypatch) -> None:
    tool_calls = [
        SimpleNamespace(id="call-1", function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}')),
        SimpleNamespace(id="call-2", function=SimpleNamespace(name="search_code", arguments='{"query":"Native"}')),
    ]
    monkeypatch.setattr(
        "codepilot.llm.litellm_native.litellm.completion",
        lambda **kwargs: _response(content="", tool_calls=tool_calls),
    )

    result = LiteLLMNativeClient("model", {}).complete([])

    assert [call.name for call in result.tool_calls] == ["read_file", "search_code"]
    assert [call.provider_tool_call_id for call in result.tool_calls] == ["call-1", "call-2"]


def test_native_client_rejects_tool_call_without_provider_id(monkeypatch) -> None:
    tool_calls = [SimpleNamespace(id="", function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}'))]
    monkeypatch.setattr(
        "codepilot.llm.litellm_native.litellm.completion",
        lambda **kwargs: _response(content="", tool_calls=tool_calls),
    )

    with pytest.raises(LLMProtocolError, match="without id"):
        LiteLLMNativeClient("model", {}).complete([])


@pytest.mark.parametrize(
    ("model_name",),
    [("openai/test-model",), ("anthropic/test-model",), ("gemini/test-model",), ("deepseek/test-model",)],
)
def test_all_configured_providers_use_the_same_native_response_contract(monkeypatch, model_name: str) -> None:
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(content="ok", tool_calls=None, model=model_name)

    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", completion)

    result = LiteLLMNativeClient(model_name, {}).complete([])

    assert result.content == "ok"
    assert result.tool_calls == ()
    assert calls[0]["model"] == model_name
    assert "tools" not in calls[0]
    assert "tool_choice" not in calls[0]
    assert calls[0]["drop_params"] is False


def test_native_client_sends_non_default_tool_choice_only_with_tools(monkeypatch) -> None:
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _response(content="", tool_calls=None)

    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", completion)
    LiteLLMNativeClient("model", {}).complete(
        [ChatMessage(role="user", content="read")],
        tools=[get_tool_spec("read_file")],
        tool_choice="required",
    )

    assert calls[0]["tool_choice"] == "required"


def test_native_client_aliases_mcp_tool_name_and_restores_codepilot_name(monkeypatch) -> None:
    internal_name = "mcp.research_lab.fetch_url"
    spec = ToolSpec(
        name=internal_name,
        description="Fetch research URL",
        risk=ToolRisk.NETWORK,
        side_effect=ToolSideEffect.NETWORK,
        default_permission=DefaultPermission.ASK,
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    )
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        provider_name = kwargs["tools"][0]["function"]["name"]
        assert provider_name != internal_name
        assert len(provider_name) <= 64
        assert re.fullmatch(r"[a-zA-Z0-9_-]+", provider_name)
        return _response(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="call-mcp-1",
                    function=SimpleNamespace(
                        name=provider_name,
                        arguments='{"url":"https://example.test/orderlab-contract"}',
                    ),
                )
            ],
            model="deepseek/deepseek-v4-flash",
        )

    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", completion)

    result = LiteLLMNativeClient("deepseek/deepseek-v4-flash", {}).complete(
        [ChatMessage(role="user", content="research")],
        tools=[spec],
    )

    assert result.tool_calls[0].name == internal_name
    assert result.tool_calls[0].arguments == {"url": "https://example.test/orderlab-contract"}
    assert spec.name == internal_name
