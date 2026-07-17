from __future__ import annotations

from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.tui_agent.layout import (
    format_side_status,
    format_result_panel,
    format_transcript_item,
    format_transcript_plain,
    transcript_item_to_copy_text,
)
from codepilot.tui_agent.models import AgentRunView, ProjectContext, TranscriptItem


def _project(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        schema_version="project.v1",
        project_path=tmp_path,
        resolved_project=tmp_path,
        git_root=tmp_path,
        is_git_repo=True,
        git_dirty_status="clean",
        workspace_root=tmp_path,
        effective_repo_path=tmp_path,
    )


def _session(tmp_path: Path):
    database = SessionDatabase(tmp_path / "sessions.sqlite3")
    database.initialize()
    return SessionService(database).create_session(tmp_path, "codepilot", "gpt-4.1", "manual")


def test_each_transcript_kind_formats_to_non_empty_text() -> None:
    items = [
        TranscriptItem(id="1", kind="user_message", timestamp="t", body="hello", copy_text="You: hello"),
        TranscriptItem(id="2", kind="assistant_plan", timestamp="t", body="先检查结构"),
        TranscriptItem(id="3", kind="assistant_raw", timestamp="t", body="raw"),
        TranscriptItem(id="4", kind="assistant_action", timestamp="t", tool_name="list_files", input_preview={"path": "."}),
        TranscriptItem(id="5", kind="tool_result", timestamp="t", tool_name="list_files", body="ok", status="success"),
        TranscriptItem(id="6", kind="permission_request", timestamp="t", tool_name="edit", body="Reason: need approval"),
        TranscriptItem(id="7", kind="permission_response", timestamp="t", status="approved"),
        TranscriptItem(id="8", kind="final_summary", timestamp="t", body="done"),
        TranscriptItem(id="9", kind="command_output", timestamp="t", title="$ /status", body="running"),
        TranscriptItem(id="10", kind="system_status", timestamp="t", body="Run finished: success"),
        TranscriptItem(id="11", kind="error", timestamp="t", body="boom"),
    ]

    assert all(format_transcript_item(item) for item in items)


def test_transcript_plain_strips_ansi_and_prefers_copy_text() -> None:
    items = (
        TranscriptItem(id="1", kind="user_message", timestamp="t", body="hello", copy_text="\x1b[31mYou: hello\x1b[0m"),
        TranscriptItem(id="2", kind="assistant_raw", timestamp="t", body="\x1b[32mraw\x1b[0m"),
    )

    plain = format_transcript_plain(items)

    assert "\x1b[" not in plain
    assert plain.splitlines()[0] == "You: hello"
    assert transcript_item_to_copy_text(items[0]) == "You: hello"


def test_side_status_hides_report_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    session = _session(tmp_path)
    view = AgentRunView(
        status="running",
        active_tool="list_files",
        changed_files=("src/calc.py", "src/app.py"),
        test_status="passed",
        tests_required=True,
        diff_required=True,
        diff_checked=True,
    )

    text = format_side_status(project, session, view, "manual")

    assert "trace.jsonl" not in text
    assert "report.md" not in text
    assert "report.json" not in text
    assert "Project: " in text
    assert "Tool: list_files" in text
    assert "Tests: passed" in text
    assert "Diff: checked" in text


def test_side_status_marks_validation_as_not_required_for_chat(tmp_path: Path) -> None:
    project = _project(tmp_path)
    session = _session(tmp_path)
    view = AgentRunView(status="message_complete", tests_required=False, diff_required=False)

    text = format_side_status(project, session, view, "manual")

    assert "Tests: not required" in text
    assert "Diff: not required" in text


def test_tool_result_failure_uses_cross_mark() -> None:
    item = TranscriptItem(
        id="1",
        kind="tool_result",
        timestamp="t",
        tool_name="run_tests",
        body="tests failed",
        status="failed",
    )

    assert format_transcript_item(item).startswith("✗ run_tests")


def test_result_panel_shows_validation_states() -> None:
    view = AgentRunView(
        status="success",
        completion_kind="task_success",
        assistant_stop_reason="structured_finish",
        tests_required=True,
        diff_required=True,
        diff_checked=True,
        test_status="passed",
        missing_evidence=(),
    )

    text = format_result_panel(view)

    assert "Tests: passed" in text
    assert "Diff: checked" in text


def test_result_panel_marks_validation_not_required_for_chat() -> None:
    view = AgentRunView(status="message_complete", completion_kind="message_complete", tests_required=False, diff_required=False)

    text = format_result_panel(view)

    assert "Tests: not required" in text
    assert "Diff: not required" in text


def test_assistant_action_contains_tool_name_and_preview() -> None:
    item = TranscriptItem(
        id="1",
        kind="assistant_action",
        timestamp="t",
        tool_name="list_files",
        input_preview={"path": ".", "max_depth": 2},
    )

    text = format_transcript_item(item)

    assert "list_files" in text
    assert '"path": "."' in text


def test_format_transcript_item_renders_complete_assistant_body() -> None:
    body = "解释" * 2000
    item = TranscriptItem(
        id="1",
        kind="assistant_raw",
        timestamp="t",
        body=body,
    )

    assert format_transcript_item(item) == f"Assistant: {body}"


def test_permission_request_uses_copy_text_when_available() -> None:
    item = TranscriptItem(
        id="1",
        kind="permission_request",
        timestamp="t",
        tool_name="edit",
        body="Reason: need approval",
        copy_text="? Permission required: edit\nReason: need approval",
        metadata={"request": "present"},
    )

    assert transcript_item_to_copy_text(item) == "? Permission required: edit\nReason: need approval"
