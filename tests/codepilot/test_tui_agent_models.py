from __future__ import annotations

from typing import get_args

from codepilot.tui_agent.models import AgentRunView, TranscriptItem, TUIEventType, to_jsonable


def test_transcript_item_can_be_constructed() -> None:
    item = TranscriptItem(
        id="msg-1",
        kind="user_message",
        timestamp="2024-01-01T00:00:00Z",
        title="You",
        body="请列出项目结构",
        copy_text="You: 请列出项目结构",
    )

    assert item.kind == "user_message"
    assert item.copy_text == "You: 请列出项目结构"


def test_agent_run_view_defaults_include_empty_transcript() -> None:
    assert AgentRunView().transcript == ()


def test_to_jsonable_serializes_transcript() -> None:
    view = AgentRunView(
        transcript=(
            TranscriptItem(
                id="msg-1",
                kind="system_status",
                timestamp="2024-01-01T00:00:00Z",
                title="Run finished",
                body="Run finished: success",
            ),
        )
    )

    assert to_jsonable(view)["transcript"][0]["kind"] == "system_status"


def test_tui_event_type_includes_chat_transcript_events() -> None:
    assert {"user_message", "command_output", "agent_finished", "agent_observation"} <= set(get_args(TUIEventType))
