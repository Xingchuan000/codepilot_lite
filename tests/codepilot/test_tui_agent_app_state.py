from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from codepilot.session.database import SessionDatabase
from codepilot.tui_agent import app as app_module
from codepilot.tui_agent.app import create_tui_agent_app
from codepilot.tui_agent.models import TUIEvent
from codepilot.tui_agent.session_store import now_iso


class _FakeWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.updated: list[str] = []
        self.value = kwargs.get("value", "")
        self.disabled = kwargs.get("disabled", False)
        self.text = args[0] if args else ""

    def update(self, text: str) -> None:
        self.updated.append(text)
        self.text = text

    def clear(self) -> None:
        return None

    def selection_updated(self, selection) -> None:
        return None

    def focus(self) -> None:
        return None

    def select_all(self) -> None:
        return None


class _FakeScrollContainer(_FakeWidget):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mounted: list[_FakeWidget] = []

    def mount(self, widget) -> None:
        self.mounted.append(widget)

    def scroll_end(self, animate: bool = False) -> None:
        return None


class _FakeContainer:
    def __enter__(self) -> "_FakeContainer":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeApp:
    def __init__(self, *args, **kwargs) -> None:
        self.last_screen = None
        self.last_screen_callback = None
        self.clipboard = ""

    def set_interval(self, *args, **kwargs) -> None:
        return None

    def push_screen(self, screen, callback=None) -> None:
        self.last_screen = screen
        self.last_screen_callback = callback

    def exit(self) -> None:
        return None

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard = text

    def query_one(self, selector, _type=None):
        raise AssertionError(f"unexpected query_one call: {selector}")


class _FakeModalScreen:
    def __class_getitem__(cls, item):  # noqa: N805
        return cls


class _FakeBinding:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


def _install_fake_textual(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "_load_textual",
        lambda: (
            _FakeApp,
            object,
            _FakeContainer,
            _FakeContainer,
            _FakeScrollContainer,
            _FakeWidget,
            _FakeWidget,
            _FakeWidget,
            _FakeWidget,
            _FakeWidget,
            _FakeModalScreen,
            _FakeBinding,
        ),
    )


def _widgets() -> dict[str, _FakeWidget]:
    return {
        "#top-status": _FakeWidget(),
        "#side-status": _FakeWidget(),
        "#transcript": _FakeScrollContainer(),
        "#task-input": _FakeWidget(value=""),
    }


def _create_app(tmp_path: Path, monkeypatch):
    _install_fake_textual(monkeypatch)
    return create_tui_agent_app(project=tmp_path, session_database=SessionDatabase(tmp_path / "data" / "sessions.sqlite3"))


def test_app_permissions_updates_runner_and_session(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._create_new_session()

    app.on_input_submitted(type("Submitted", (), {"value": "/permissions read_only"})())

    assert app.session.permission_mode == "read_only"
    assert app.runner.config.permission_mode == "read_only"
    assert app.runner.session.permission_mode == "read_only"


def test_app_move_only_updates_next_session_project(tmp_path: Path, monkeypatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._create_new_session()

    app.on_input_submitted(SimpleNamespace(value="/move other"))

    assert app._project_context.resolved_project == tmp_path.resolve()
    assert app.runner.project.resolved_project == tmp_path.resolve()
    with app._session_database.connect() as connection:
        assert connection.execute("SELECT path FROM projects WHERE project_id = ?", (app.session.project_id,)).fetchone()[0] == str(tmp_path.resolve())
    assert app._new_session_project_context.resolved_project == other.resolve()


def test_app_copy_to_clipboard_writes_system_clipboard(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    monkeypatch.setattr("codepilot.tui_agent.app.sys.platform", "darwin")
    monkeypatch.setattr("codepilot.tui_agent.app.shutil.which", lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else None)
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        "codepilot.tui_agent.app.subprocess.run",
        lambda cmd, input=None, text=None, check=None: calls.append((cmd, input or "")),
    )

    app.copy_to_clipboard("hello")

    assert app.clipboard == "hello"
    assert calls == [(["pbcopy"], "hello")]


def test_exit_command_returns_without_refreshing_after_exit(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    exited = {"called": False}

    def _exit() -> None:
        exited["called"] = True

    def _drain_events() -> None:
        raise AssertionError("exit should not drain events after quitting")

    app.exit = _exit  # type: ignore[method-assign]
    app._drain_events = _drain_events  # type: ignore[method-assign]

    app.on_input_submitted(SimpleNamespace(value="/exit"))

    assert exited["called"] is True
    assert len(widgets["#transcript"].mounted) == 0


def test_branch_confirmation_modal_can_cancel_without_resubmitting(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._create_new_session()
    app._event_stream.publish(
        TUIEvent(
            type="branch_confirmation_required",
            timestamp=now_iso(),
            session_id=app.session.session_id,
            payload={"text": "完整原始任务", "old_branch": "main", "new_branch": "feature"},
        )
    )

    app._drain_events()

    assert app._reducer.view.status == "waiting_branch_confirmation"
    assert app.last_screen.__class__.__name__ == "BranchConfirmationModal"
    assert app.last_screen.pending.text == "完整原始任务"
    assert app.last_screen_callback is not None
    app.last_screen_callback(False)
    assert app._reducer.view.status == "idle"

    first_screen = app.last_screen
    app._event_stream.publish(
        TUIEvent(
            type="branch_confirmation_required",
            timestamp=now_iso(),
            session_id=app.session.session_id,
            payload={"text": "完整原始任务", "old_branch": "main", "new_branch": "feature"},
        )
    )
    app._drain_events()
    assert app.last_screen is not first_screen


def test_branch_confirmation_modal_resubmits_complete_pending_text(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._create_new_session()
    submitted = []
    app.runner.resume_after_branch_confirmation = lambda pending: submitted.append(pending) or "turn-pending"  # type: ignore[method-assign]
    app._event_stream.publish(
        TUIEvent(
            type="branch_confirmation_required",
            timestamp=now_iso(),
            session_id=app.session.session_id,
            payload={"text": "不能被截断的完整任务", "old_branch": "main", "new_branch": "feature"},
        )
    )

    app._drain_events()
    app.last_screen_callback(True)

    assert len(submitted) == 1
    assert submitted[0].text == "不能被截断的完整任务"
    assert submitted[0].new_branch == "feature"
    assert app._reducer.view.status == "running"


def test_open_session_shows_recovery_modal_for_unknown_side_effect(tmp_path: Path, monkeypatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    session = app._session_service.create_session(tmp_path, "codepilot", "default", "manual")
    store = app._session_service.store
    turn = store.create_turn(
        session_id=session.session_id,
        title="Turn 1",
        provider_snapshot="codepilot",
        model_snapshot="default",
        permission_mode_snapshot="manual",
        branch_snapshot=None,
        status="running",
    )
    attempt = store.create_attempt(turn_id=turn.turn_id, status="running", started_at="2024-01-01T00:00:00+00:00")
    command = "git commit -m x"
    call = store.create_tool_call(turn_id=turn.turn_id, attempt_id=attempt.attempt_id, tool_name="run_shell", arguments={"repo": str(tmp_path), "command": command})
    store.persist_tool_execution_started(
        call.tool_call_id,
        {"command_sha256": hashlib.sha256(command.encode()).hexdigest(), "auto_retry_allowed": False},
    )

    app._activate_session(session.session_id)

    assert app.last_screen.__class__.__name__ == "RecoveryModal"
    assert app.last_screen.tool_call_id == call.tool_call_id
    assert store.get_turn(turn.turn_id).status == "recovery_required"
    app.last_screen_callback("abort")
    assert store.get_turn(turn.turn_id).status == "cancelled"
