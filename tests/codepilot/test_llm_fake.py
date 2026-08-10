import json

import pytest

from codepilot.llm.fake import FakeLLMExhaustedError, StructuredFakeLLM
from codepilot.llm.types import ChatMessage, LLMResponse, LLMToolCall
from codepilot.tools.registry import get_tool_spec


def test_structured_fake_llm_returns_responses_in_order_and_records_native_contract() -> None:
    response = LLMResponse(
        content="",
        tool_calls=(LLMToolCall(provider_tool_call_id="call-1", name="read_file", arguments={"path": "a.py"}),),
    )
    client = StructuredFakeLLM([response])
    spec = get_tool_spec("read_file")

    assert client.complete([ChatMessage(role="user", content="hello")], tools=[spec]) == response
    assert client.calls[0]["messages"] == [ChatMessage(role="user", content="hello")]
    assert client.calls[0]["tools"] == (spec,)
    assert client.calls[0]["tool_choice"] == "auto"


def test_structured_fake_llm_raises_when_exhausted() -> None:
    client = StructuredFakeLLM([LLMResponse(content="only")])
    client.complete([])

    with pytest.raises(FakeLLMExhaustedError, match="responses exhausted"):
        client.complete([])


def test_structured_fake_llm_from_jsonl_requires_native_response_shape(tmp_path) -> None:
    path = tmp_path / "responses.jsonl"
    path.write_text(
        json.dumps(
            {
                "content": "",
                "tool_calls": [
                    {"provider_tool_call_id": "call-1", "name": "read_file", "arguments": {"path": "a.py"}}
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert StructuredFakeLLM.from_jsonl(path).complete([]).tool_calls[0].provider_tool_call_id == "call-1"


def test_structured_fake_llm_rejects_malformed_action_fixture(tmp_path) -> None:
    path = tmp_path / "responses.jsonl"
    path.write_text('{"type":"finish","status":"success","summary":"done"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="content and tool_calls"):
        StructuredFakeLLM.from_jsonl(path)
