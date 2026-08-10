import pytest

from codepilot.llm.tool_schema import to_provider_tool_name
from codepilot.llm.types import ChatMessage, ChatMessagePart, RichChatMessage
from codepilot.session.provider_messages import to_provider_messages


def test_provider_serializer_replays_native_assistant_tool_call_and_tool_result() -> None:
    messages = to_provider_messages(
        [
            ChatMessage(role="user", content="Read a.py"),
            RichChatMessage(
                role="assistant",
                parts=(
                    ChatMessagePart(type="text", content="I will inspect it."),
                    ChatMessagePart(
                        type="tool_call",
                        content={
                            "provider_tool_call_id": "provider-call-1",
                            "tool_name": "read_file",
                            "arguments": {"path": "a.py"},
                        },
                    ),
                ),
            ),
            RichChatMessage(
                role="tool",
                parts=(
                    ChatMessagePart(
                        type="tool_result",
                        content={
                            "provider_tool_call_id": "provider-call-1",
                            "tool_name": "read_file",
                            "content": "file content",
                        },
                    ),
                ),
            ),
        ]
    )

    assert messages == [
        {"role": "user", "content": "Read a.py"},
        {
            "role": "assistant",
            "content": "I will inspect it.",
            "tool_calls": [
                {
                    "id": "provider-call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "provider-call-1", "name": "read_file", "content": "file content"},
    ]


def test_provider_serializer_rejects_missing_native_provider_id() -> None:
    with pytest.raises(KeyError, match="provider_tool_call_id"):
        to_provider_messages(
            [
                RichChatMessage(
                    role="assistant",
                    parts=(ChatMessagePart(type="tool_call", content={"tool_name": "read_file", "arguments": {}}),),
                )
            ]
        )


def test_provider_serializer_aliases_internal_mcp_names_in_replay() -> None:
    internal_name = "mcp.research_lab.fetch_url"
    provider_name = to_provider_tool_name(internal_name)

    messages = to_provider_messages(
        [
            RichChatMessage(
                role="assistant",
                parts=(
                    ChatMessagePart(
                        type="tool_call",
                        content={
                            "provider_tool_call_id": "provider-call-mcp",
                            "tool_name": internal_name,
                            "arguments": {"url": "https://example.test"},
                        },
                    ),
                ),
            ),
            RichChatMessage(
                role="tool",
                parts=(
                    ChatMessagePart(
                        type="tool_result",
                        content={
                            "provider_tool_call_id": "provider-call-mcp",
                            "tool_name": internal_name,
                            "content": "ok",
                        },
                    ),
                ),
            ),
        ]
    )

    assert provider_name != internal_name
    assert messages[0]["tool_calls"][0]["function"]["name"] == provider_name
    assert messages[1]["name"] == provider_name
