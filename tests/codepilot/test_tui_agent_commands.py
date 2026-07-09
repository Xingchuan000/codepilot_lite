from __future__ import annotations

from pathlib import Path

from codepilot.tui_agent.commands import handle_command, parse_slash_command
from codepilot.tui_agent.models import AgentRunView, ProjectContext, TUISession


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
        default_runs_dir=tmp_path / "runs",
    )


def _session(tmp_path: Path) -> TUISession:
    return TUISession(
        schema_version="session.v1",
        session_id="session-1",
        project_path=tmp_path,
        git_root=tmp_path,
        workspace_root=tmp_path,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        title="demo",
        model="gpt-4.1",
        permission_mode="manual",
        runs_dir=tmp_path / "runs",
        session_dir=tmp_path / ".codepilot" / "sessions" / "session-1",
        messages_path=tmp_path / ".codepilot" / "sessions" / "session-1" / "messages.jsonl",
        runs_index_path=tmp_path / ".codepilot" / "sessions" / "session-1" / "runs.jsonl",
    )


def test_parse_slash_command_splits_copy_target() -> None:
    assert parse_slash_command("/copy last") == ("copy", ["last"])


def test_copy_command_opens_copy_mode(tmp_path: Path) -> None:
    result = handle_command(
        "/copy",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert result.open_copy_mode is True
    assert result.copy_target == "all"


def test_copy_command_supports_last_and_errors_targets(tmp_path: Path) -> None:
    last_result = handle_command(
        "/copy last",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )
    errors_result = handle_command(
        "/copy errors",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert last_result.copy_target == "last"
    assert errors_result.copy_target == "errors"


def test_export_transcript_command_requests_export(tmp_path: Path) -> None:
    result = handle_command(
        "/export-transcript",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert result.export_transcript_requested is True


def test_exit_command_requests_exit(tmp_path: Path) -> None:
    result = handle_command(
        "/exit",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert result.exit_requested is True
