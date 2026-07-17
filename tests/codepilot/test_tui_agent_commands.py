from __future__ import annotations

from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.tui_agent.commands import handle_command, parse_slash_command
from codepilot.tui_agent.models import AgentRunView, ProjectContext


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


def test_move_command_only_sets_next_session_project(tmp_path: Path) -> None:
    project = _project(tmp_path)
    moved = tmp_path / "moved"
    moved.mkdir()

    result = handle_command(
        "/move moved",
        view=AgentRunView(),
        project=project,
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert result.next_new_session_project == moved.resolve()
    assert "current session project was not changed" in result.output.lower()


def test_exit_command_requests_exit(tmp_path: Path) -> None:
    result = handle_command(
        "/exit",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert result.exit_requested is True


def test_model_command_without_argument_opens_model_picker(tmp_path: Path) -> None:
    result = handle_command(
        "/model",
        view=AgentRunView(),
        project=_project(tmp_path),
        session=_session(tmp_path),
        permission_mode="manual",
    )

    assert result.open_model_picker is True
    assert result.model_name is None
