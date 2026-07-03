import json

import pytest

from codepilot.llm.fake import FakeLLMClient, FakeLLMExhaustedError
from codepilot.llm.types import ChatMessage


def test_fake_llm_returns_responses_in_order() -> None:
    client = FakeLLMClient(["one", "two"])

    assert client.complete([]).content == "one"
    assert client.complete([]).content == "two"


def test_fake_llm_records_messages() -> None:
    client = FakeLLMClient(["ok"])
    messages = [ChatMessage(role="user", content="hello")]

    client.complete(messages)

    assert client.calls == [messages]


def test_fake_llm_raises_when_exhausted() -> None:
    client = FakeLLMClient(["only"])
    client.complete([])

    with pytest.raises(FakeLLMExhaustedError, match="responses exhausted"):
        client.complete([])


def test_fake_llm_from_jsonl_reads_raw_json_lines(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    path.write_text('{"type":"finish","status":"success","summary":"done"}\n', encoding="utf-8")

    response = FakeLLMClient.from_jsonl(path).complete([])

    assert json.loads(response.content)["type"] == "finish"


def test_fake_llm_from_jsonl_reads_content_field(tmp_path) -> None:
    path = tmp_path / "actions.jsonl"
    path.write_text('{"content":"hello"}\n', encoding="utf-8")

    assert FakeLLMClient.from_jsonl(path).complete([]).content == "hello"
