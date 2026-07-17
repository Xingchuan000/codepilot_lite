from __future__ import annotations

from pathlib import Path

from codepilot.session.context_adapters import SessionHistory, TextActionContextAdapter
from codepilot.session.database import SessionDatabase
from codepilot.session.model_capabilities import ModelContextProfile
from codepilot.session.store import SessionStore


def test_text_replay_keeps_raw_action_once_and_uses_normalized_observation(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    turn = store.create_turn(session_id=session.session_id, title="turn", provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None)
    assistant = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="assistant", status="completed", content='{"type":"tool_call"}')
    store.append_message_part(assistant.message_id, type="text", content='{"type":"tool_call"}')
    store.append_message_part(assistant.message_id, type="tool_call", content={"tool_name": "read_file", "arguments": {}})
    tool = store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="tool", status="completed", content="NORMALIZED OBSERVATION")
    store.append_message_part(tool.message_id, type="tool_result", content="NORMALIZED OBSERVATION")

    history = SessionHistory(session.session_id, turn.turn_id, Path(tmp_path), (), tuple(store.list_messages_with_parts(session.session_id)))
    messages = TextActionContextAdapter(store).build_messages(history, ModelContextProfile("openai", "fake", 16_384, False))
    assistant_contents = [message.content for message in messages if message.role == "assistant"]
    assert assistant_contents == ['{"type":"tool_call"}']
    assert [message.content for message in messages if message.role == "user"] == ["NORMALIZED OBSERVATION"]


def test_context_replay_keeps_conversation_order_and_puts_current_turn_last(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "session.sqlite3")
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(project_path=tmp_path, provider="openai", current_model="fake", permission_mode="manual")
    first_turn = store.create_turn(session_id=session.session_id, title="first", provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None)
    store.create_message(session_id=session.session_id, turn_id=first_turn.turn_id, role="user", status="completed", content="第一轮问题")
    store.create_message(session_id=session.session_id, turn_id=first_turn.turn_id, role="assistant", status="completed", content="第一轮回答")
    current_turn = store.create_turn(session_id=session.session_id, title="second", provider_snapshot="openai", model_snapshot="fake", permission_mode_snapshot="manual", branch_snapshot=None)
    store.create_message(session_id=session.session_id, turn_id=current_turn.turn_id, role="user", status="completed", content="刚刚我问了什么")

    history = SessionHistory(session.session_id, current_turn.turn_id, Path(tmp_path), (), tuple(store.list_messages_with_parts(session.session_id)))
    messages = TextActionContextAdapter(store).build_messages(history, ModelContextProfile("openai", "fake", 16_384, False))

    assert [(message.role, message.content) for message in messages[1:]] == [
        ("user", "第一轮问题"),
        ("assistant", "第一轮回答"),
        ("user", "刚刚我问了什么\nRepository: " + str(tmp_path)),
    ]
