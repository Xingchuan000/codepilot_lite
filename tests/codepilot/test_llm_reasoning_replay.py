from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codepilot.llm.litellm_native import LiteLLMNativeClient
from codepilot.llm.types import ChatMessage, ChatMessagePart, LLMReasoningReplay, RichChatMessage
from codepilot.session.context_adapters import ProviderContextAdapter, SessionHistory
from codepilot.session.database import SessionDatabase
from codepilot.session.model_context import ModelContextProfile
from codepilot.llm.provider_messages import to_provider_messages
from codepilot.session.repositories import SessionRepositories
from codepilot.session.trace_recorder import SessionTraceRecorder


def test_native_client_extracts_litellm_thinking_blocks(monkeypatch) -> None:
    blocks = ({"type": "thinking", "thinking": "...", "signature": "sig"},)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=None, thinking_blocks=list(blocks)),
                finish_reason="stop",
            )
        ],
        model="anthropic/claude-sonnet-4",
        usage=None,
    )
    monkeypatch.setattr("codepilot.llm.litellm_native.litellm.completion", lambda **kwargs: response)

    result = LiteLLMNativeClient("anthropic/claude-sonnet-4", {}).complete([ChatMessage("user", "hi")])

    assert result.reasoning_replay == LLMReasoningReplay("anthropic_thinking_blocks", blocks)


def test_provider_serializer_replays_thinking_blocks_before_tool_result() -> None:
    blocks = [{"type": "thinking", "thinking": "...", "signature": "sig"}]
    messages = to_provider_messages(
        [
            RichChatMessage(
                role="assistant",
                parts=(
                    ChatMessagePart(
                        type="reasoning_replay",
                        content={"blocks": blocks},
                        provider_format="anthropic_thinking_blocks",
                    ),
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

    assert messages[0]["thinking_blocks"] == blocks
    assert messages[0]["content"] == "I will inspect it."
    assert messages[1]["tool_call_id"] == "provider-call-1"


def test_sqlite_round_trip_preserves_thinking_block_signature(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    session = store.sessions.create_session(project_path=tmp_path, provider="anthropic", current_model="claude", permission_mode="manual")
    turn = store.turns.create_turn(
        session_id=session.session_id,
        title="turn",
        provider_snapshot="anthropic",
        model_snapshot="claude",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
    )
    recorder = SessionTraceRecorder(database, session.session_id, turn.turn_id)
    replay = LLMReasoningReplay(
        provider_format="anthropic_thinking_blocks",
        blocks=({"type": "thinking", "thinking": "...", "signature": "sig"},),
    )

    recorder.assistant_message_started(streaming=False)
    recorder.assistant_message_completed(content="I will inspect it.", reasoning_replay=replay)
    recorder.record_native_tool_call(
        provider_tool_call_id="provider-call-1",
        tool_name="read_file",
        arguments={"path": "a.py"},
    )
    recorder.tool_result_created(
        tool_name="read_file",
        success=True,
        content="file content",
        provider_tool_call_id="provider-call-1",
        tool_call_id="internal-call-1",
    )

    history = SessionHistory(session.session_id, turn.turn_id, tmp_path, (), tuple(store.messages.list_messages_with_parts(session.session_id)))
    restored = ProviderContextAdapter(store).build_messages(
        history,
        ModelContextProfile("anthropic", "claude", 16_384, False),
    )

    assert to_provider_messages(restored)[1]["thinking_blocks"][0]["signature"] == "sig"

