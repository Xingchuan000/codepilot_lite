from __future__ import annotations

from pathlib import Path

import pytest

from codepilot.llm.types import RichChatMessage
from codepilot.session.context_adapters import ProviderContextAdapter, SessionHistory
from codepilot.session.database import SessionDatabase
from codepilot.session.errors import SessionProtocolMismatch
from codepilot.session.model_context import ModelContextProfile
from codepilot.session.repositories import SessionRepositories


def _session(tmp_path: Path) -> tuple[SessionDatabase, SessionRepositories, str, str]:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionRepositories(database)
    session = store.sessions.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.turns.create_turn(session_id=session.session_id, title="turn", provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None)
    return database, store, session.session_id, turn.turn_id


def test_native_replay_keeps_provider_id_and_tool_role(tmp_path: Path) -> None:
    database, store, session_id, turn_id = _session(tmp_path)
    assistant = store.messages.create_message(session_id=session_id, turn_id=turn_id, role="assistant", status="completed", content="")
    store.messages.append_message_part(
        assistant.message_id,
        type="tool_call",
        content={"provider_tool_call_id": "provider-call-1", "tool_name": "read_file", "arguments": {"path": "a.py"}},
    )
    tool = store.messages.create_message(session_id=session_id, turn_id=turn_id, role="tool", status="completed", content="ok")
    store.messages.append_message_part(
        tool.message_id,
        type="tool_result",
        content={
            "provider_tool_call_id": "provider-call-1",
            "tool_name": "read_file",
            "content": "ok",
            "codepilot_tool_call_id": "tool-call-1",
        },
    )

    history = SessionHistory(session_id, turn_id, tmp_path, (), tuple(store.messages.list_messages_with_parts(session_id)))
    messages = ProviderContextAdapter(store).build_messages(history, ModelContextProfile("openai", "fake", 16_384, False))

    assert isinstance(messages[1], RichChatMessage)
    assert messages[1].role == "assistant"
    assert messages[1].parts[0].content["provider_tool_call_id"] == "provider-call-1"
    assert messages[2].role == "tool"
    assert messages[2].parts[0].content["provider_tool_call_id"] == "provider-call-1"


def test_native_replay_fails_fast_for_missing_provider_tool_id(tmp_path: Path) -> None:
    database, store, session_id, turn_id = _session(tmp_path)
    assistant = store.messages.create_message(session_id=session_id, turn_id=turn_id, role="assistant", status="completed", content="")
    store.messages.append_message_part(assistant.message_id, type="tool_call", content={"tool_name": "read_file", "arguments": {}})

    history = SessionHistory(session_id, turn_id, tmp_path, (), tuple(store.messages.list_messages_with_parts(session_id)))

    with pytest.raises(SessionProtocolMismatch, match="missing provider_tool_call_id"):
        ProviderContextAdapter(store).build_messages(history, ModelContextProfile("openai", "fake", 16_384, False))


def test_text_replay_fails_fast_for_message_row_content_without_parts(tmp_path: Path) -> None:
    database, store, session_id, turn_id = _session(tmp_path)
    store.messages.create_message(session_id=session_id, turn_id=turn_id, role="assistant", status="completed", content="old text")

    history = SessionHistory(session_id, turn_id, tmp_path, (), tuple(store.messages.list_messages_with_parts(session_id)))

    with pytest.raises(SessionProtocolMismatch, match="unsupported pre-native-message format"):
        ProviderContextAdapter(store).build_messages(history, ModelContextProfile("openai", "fake", 16_384, False))
