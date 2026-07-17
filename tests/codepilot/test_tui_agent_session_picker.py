from __future__ import annotations

import asyncio
from types import SimpleNamespace
from pathlib import Path

from codepilot.session.database import SessionDatabase
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore
from codepilot.tui_agent.app import create_tui_agent_app
from codepilot.tui_agent.models import TUIEvent
from codepilot.tui_agent.session_picker import SessionPickerResult, SessionPickerScreen
from codepilot.tui_agent.model_picker import ModelPickerScreen

from tests.codepilot.test_tui_agent_app_state import _install_fake_textual, _widgets


FAKE_ACTIONS = Path(__file__).parent / "fixtures" / "tui_agent_actions_success.jsonl"


def _app(tmp_path: Path, monkeypatch, *, fake_actions: Path | None = FAKE_ACTIONS):
    _install_fake_textual(monkeypatch)
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    app = create_tui_agent_app(project=tmp_path, fake_actions=fake_actions, session_database=database)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    return app, database, widgets


def test_startup_uses_injected_database_and_does_not_create_session(tmp_path: Path, monkeypatch) -> None:
    app, database, widgets = _app(tmp_path, monkeypatch)

    app.on_mount()

    assert app.session is None
    assert app._session_database is database
    assert app._session_controller.database is database
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


def test_new_session_uses_minisweagent_default_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MSWEA_MODEL_NAME", "deepseek/deepseek-v4-flash")
    app, database, _ = _app(tmp_path, monkeypatch, fake_actions=None)

    app._handle_session_picker_result(SessionPickerResult("new"))

    assert app.session.current_model == "deepseek/deepseek-v4-flash"
    assert SessionService(database).list_all_sessions()[0].current_model == "deepseek/deepseek-v4-flash"


def test_model_command_opens_read_only_model_picker(tmp_path: Path, monkeypatch) -> None:
    app, _, _ = _app(tmp_path, monkeypatch)
    app._create_new_session()

    app.on_input_submitted(SimpleNamespace(value="/model"))

    assert isinstance(app.last_screen, ModelPickerScreen)
    assert "fake" in app.last_screen.model_names


def test_new_session_without_model_keeps_picker_and_writes_no_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MSWEA_MODEL_NAME", "")
    app, database, widgets = _app(tmp_path, monkeypatch, fake_actions=None)

    app._handle_session_picker_result(SessionPickerResult("new"))

    assert app.session is None
    assert SessionService(database).list_all_sessions() == []
    assert "尚未配置模型" in app._reducer.view.transcript[-1].body
    assert isinstance(app.last_screen, SessionPickerScreen)
    assert "尚未配置模型" in app.last_screen.notice


def test_picker_opens_cross_project_session_from_same_database(tmp_path: Path, monkeypatch) -> None:
    app, database, widgets = _app(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    session = SessionService(database).create_session(other, "codepilot", "default", "manual")

    app._handle_session_picker_result(SessionPickerResult("open", session.session_id))

    assert app.session is not None
    assert app.session.session_id == session.session_id
    assert app._project_context.resolved_project == other.resolve()
    assert app.runner.session_controller.database is database
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
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, SessionPickerScreen)
            assert app.session is None

    asyncio.run(check())
    assert SessionService(database).list_all_sessions() == []


def test_real_textual_picker_new_action_creates_one_session(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

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
    session = SessionService(database).create_session(tmp_path, "fake", "fake", "manual")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.session is not None
            assert app.session.session_id == session.session_id

    asyncio.run(check())


def test_real_textual_model_command_opens_model_picker(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    database.initialize()
    session = SessionService(database).create_session(tmp_path, "fake", "fake", "manual")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app.pop_screen()
            app._activate_session(session.session_id)
            await pilot.pause()
            await pilot.click("#task-input")
            await pilot.press(*"/model")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ModelPickerScreen)

    asyncio.run(check())


def _seed_message(database: SessionDatabase, project: Path, text: str):
    database.initialize()
    store = SessionStore(database)
    session = store.create_session(
        project_path=project,
        provider="fake",
        current_model="fake",
        permission_mode="manual",
    )
    turn = store.create_turn(
        session_id=session.session_id,
        title=text,
        provider_snapshot="fake",
        model_snapshot="fake",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="completed",
    )
    store.create_message(session_id=session.session_id, turn_id=turn.turn_id, role="user", status="completed", content=text)
    return session


def test_real_textual_missing_model_does_not_exit_or_create_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MSWEA_MODEL_NAME", "")
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    app = create_tui_agent_app(project=tmp_path, fake_actions=None, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert app.is_running is True
            assert app.session is None
            assert "尚未配置模型" in str(app.query_one("#transcript").children[-1].render())
            assert isinstance(app.screen, SessionPickerScreen)

    asyncio.run(check())
    assert SessionService(database).list_all_sessions() == []


def test_real_textual_hydrates_history_and_does_not_duplicate_on_refresh(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    session = _seed_message(database, tmp_path, "历史消息 A")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app._activate_session(session.session_id)
            await pilot.pause()
            await pilot.pause()
            transcript = app.query_one("#transcript")
            rendered = [str(child.render()) for child in transcript.children]
            assert sum("历史消息 A" in text for text in rendered) == 1
            child_count = len(transcript.children)
            app._refresh()
            await pilot.pause()
            assert len(transcript.children) == child_count

    asyncio.run(check())


def test_real_textual_session_switch_replaces_transcript_without_cross_session_messages(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    session_a = _seed_message(database, tmp_path, "消息 A")
    session_b = _seed_message(database, tmp_path, "消息 B")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app._activate_session(session_a.session_id)
            await pilot.pause()
            await pilot.pause()
            app._activate_session(session_b.session_id)
            await pilot.pause()
            await pilot.pause()
            rendered = [str(child.render()) for child in app.query_one("#transcript").children]
            assert any("消息 B" in text for text in rendered)
            assert all("消息 A" not in text for text in rendered)

    asyncio.run(check())


def test_real_textual_fast_session_switch_discards_delayed_old_render(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    session_a = _seed_message(database, tmp_path, "快速切换 A")
    session_b = _seed_message(database, tmp_path, "快速切换 B")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app._activate_session(session_a.session_id)
            app._activate_session(session_b.session_id)
            await pilot.pause()
            await pilot.pause()
            rendered = [str(child.render()) for child in app.query_one("#transcript").children]
            assert any("快速切换 B" in text for text in rendered)
            assert all("快速切换 A" not in text for text in rendered)

    asyncio.run(check())


def test_real_textual_new_event_is_mounted_once(tmp_path: Path) -> None:
    database = SessionDatabase(tmp_path / "data" / "sessions.sqlite3")
    session = _seed_message(database, tmp_path, "历史消息")
    app = create_tui_agent_app(project=tmp_path, fake_actions=FAKE_ACTIONS, session_database=database)

    async def check() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            app._activate_session(session.session_id)
            await pilot.pause()
            await pilot.pause()
            app._event_stream.publish(
                TUIEvent(
                    type="user_message",
                    timestamp="2026-07-14T00:00:00+00:00",
                    session_id=session.session_id,
                    payload={"text": "新事件"},
                )
            )
            app._drain_events()
            app._refresh()
            await pilot.pause()
            rendered = [str(child.render()) for child in app.query_one("#transcript").children]
            assert sum("新事件" in text for text in rendered) == 1

    asyncio.run(check())
