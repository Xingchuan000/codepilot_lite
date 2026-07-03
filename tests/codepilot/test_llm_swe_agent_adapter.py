from __future__ import annotations

from types import SimpleNamespace

import pytest

from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter, safe_raw_response
from codepilot.llm.types import ChatMessage
from minisweagent.models.test_models import DeterministicModel


class FakeSweModel:
    def __init__(self) -> None:
        self.calls = []

    def query_without_default_tools(self, messages):
        self.calls.append(messages)
        return {
            "role": "assistant",
            "content": '{"type":"finish","status":"success","summary":"done"}',
            "model": "fake-swe",
            "extra": {"cost": 0.0, "usage": {"total_tokens": 1}},
        }


def test_adapter_converts_messages_and_uses_query_without_default_tools() -> None:
    model = FakeSweModel()
    adapter = SweAgentModelAdapter(model=model)

    response = adapter.complete([ChatMessage(role="user", content="hi")])

    assert model.calls == [[{"role": "user", "content": "hi"}]]
    assert response.content == '{"type":"finish","status":"success","summary":"done"}'
    assert response.model == "fake-swe"
    assert response.usage == {"total_tokens": 1}


def test_adapter_supports_message_content_nested_dict() -> None:
    class NestedMessageModel:
        def query_without_default_tools(self, messages):
            return {"message": {"content": "nested"}}

    assert SweAgentModelAdapter(model=NestedMessageModel()).complete([]).content == "nested"


def test_adapter_refuses_non_test_model_without_query_without_default_tools() -> None:
    class RealishModel:
        pass

    with pytest.raises(RuntimeError, match="refusing to use bash-only query path"):
        SweAgentModelAdapter(model=RealishModel()).complete([])


def test_adapter_allows_deterministic_model_fallback_query() -> None:
    model = DeterministicModel(outputs=[{"role": "assistant", "content": "fallback", "extra": {"actions": []}}])

    response = SweAgentModelAdapter(model=model).complete([ChatMessage(role="user", content="hi")])

    assert response.content == "fallback"
    assert response.model == "deterministic"


def test_safe_raw_response_truncates_large_extra_response() -> None:
    raw = {"content": "ok", "extra": {"response": {"huge": "x" * 5000}}}

    result = safe_raw_response(raw, max_chars=80)

    assert result["content"] == "ok"
    assert "response_preview" in result["extra"]
    assert len(result["extra"]["response_preview"]) <= 80


def test_litellm_query_without_default_tools_uses_tools_none(monkeypatch) -> None:
    pytest.importorskip("litellm")
    from minisweagent.models.litellm_model import LitellmModel

    model = LitellmModel(model_name="demo-model")
    calls = []

    class FakeUsage:
        def model_dump(self):
            return {"total_tokens": 5}

    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done"))],
        usage=FakeUsage(),
        model_dump=lambda: {"id": "resp-1"},
    )

    def fake_query(messages, tools=None, **kwargs):
        calls.append({"messages": messages, "tools": tools, "kwargs": kwargs})
        return fake_response

    def fail_parse_actions(response):
        raise AssertionError("_parse_actions should not be called")

    monkeypatch.setattr(model, "_query", fake_query)
    monkeypatch.setattr(model, "_parse_actions", fail_parse_actions)
    monkeypatch.setattr(model, "_calculate_cost", lambda response: {"cost": 0.0})

    result = model.query_without_default_tools([{"role": "user", "content": "hi"}])

    assert calls[0]["tools"] is None
    assert result["content"] == "done"
    assert result["extra"]["usage"] == {"total_tokens": 5}
