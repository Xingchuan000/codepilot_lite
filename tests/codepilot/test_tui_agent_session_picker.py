from __future__ import annotations

import asyncio
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.tui_agent.app import create_tui_agent_app
from codepilot.tui_agent.session_picker import SessionPickerResult, SessionPickerScreen

from tests.codepilot.test_tui_agent_app_state import _install_fake_textual, _widgets


def _app(tmp_path: Path, monkeypatch):
    _install_fake_textual(monkeypatch)
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    app = create_tui_agent_app(project=tmp_path, session_database=database)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    return app, database, widgets


def test_startup_uses_injected_database_and_does_not_create_session(tmp_path: Path, monkeypatch) -> None:
    app, database, widgets = _app(tmp_path, monkeypatch)

    app.on_mount()

    assert app.session is None
    assert app._session_database is database
    assert app._session_store.database is database
    assert SessionService(database).list_all_sessions() == []
    assert widgets["#task-input"].disabled is True
    assert isinstance(app.last_screen, SessionPickerScreen)


def test_new_session_is_created_only_after_picker_result(tmp_path: Path, monkeypatch) -> None:
    app, database, widgets = _app(tmp_path, monkeypatch)

    app._handle_session_picker_result(SessionPickerResult("new"))

    assert app.session is not None
    assert len(SessionService(database).list_all_sessions()) == 1
    assert app.runner.active_session_id == app.session.session_id
    assert widgets["#task-input"].disabled is False
    assert not (tmp_path / ".codepilot" / "sessions.sqlite").exists()


def test_picker_opens_cross_project_session_from_same_database(tmp_path: Path, monkeypatch) -> None:
    app, database, widgets = _app(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    session = SessionService(database).create_session(other, "codepilot", "default", "manual")

    app._handle_session_picker_result(SessionPickerResult("open", session.session_id))

    assert app.session is not None
    assert app.session.session_id == session.session_id
    assert app._project_context.resolved_project == other.resolve()
    assert app.runner.session_store.database is database
    assert widgets["#task-input"].disabled is False


def test_missing_project_session_is_opened_read_only(tmp_path: Path, monkeypatch) -> None:
    app, database, widgets = _app(tmp_path, monkeypatch)
    missing = tmp_path / "deleted-project"
    session = SessionService(database).create_session(missing, "codepilot", "default", "manual")

    app._handle_session_picker_result(SessionPickerResult("open", session.session_id))

    assert app.session is not None
    assert app._project_context.resolved_project == missing.resolve()
    assert app._session_read_only is True
    assert widgets["#task-input"].disabled is True


def test_real_textual_app_mounts_session_picker_before_creating_session(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    app = create_tui_agent_app(project=tmp_path, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, SessionPickerScreen)
            assert app.session is None

    asyncio.run(check())
    assert SessionService(database).list_all_sessions() == []


def test_real_textual_picker_new_action_creates_one_session(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    app = create_tui_agent_app(project=tmp_path, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert app.session is not None

    asyncio.run(check())
    assert len(SessionService(database).list_all_sessions()) == 1


def test_real_textual_picker_enter_opens_selected_session(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    session = SessionService(database).create_session(tmp_path, "codepilot", "default", "manual")
    app = create_tui_agent_app(project=tmp_path, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.session is not None
            assert app.session.session_id == session.session_id

    asyncio.run(check())
