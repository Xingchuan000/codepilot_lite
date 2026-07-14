from __future__ import annotations

from typing import get_args

from codepilot.permissions import PermissionRequest
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
    view = AgentRunView()

    assert view.transcript == ()
    assert view.diff_checked is None


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


def test_permission_request_comes_from_codepilot_permissions() -> None:
    request = PermissionRequest(
        request_id="perm-1",
        run_id="run-1",
        action_id="act-1",
        tool_name="run_shell",
        arguments_preview={},
        reason="need approval",
        risk="shell_execution",
        side_effect="local_exec",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
    )

    assert AgentRunView(permission_requests=(request,)).permission_requests[0].request_id == "perm-1"


def test_legacy_session_models_are_removed() -> None:
    import codepilot.tui_agent.models as models

    assert not hasattr(models, "TUISession")
    assert not hasattr(models, "TUISessionRunRef")
