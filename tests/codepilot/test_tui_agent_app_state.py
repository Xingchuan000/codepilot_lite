from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from codepilot.tui_agent import app as app_module
from codepilot.tui_agent.app import create_tui_agent_app


class _FakeWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.updated: list[str] = []
        self.value = kwargs.get("value", "")
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
        self.clipboard = ""

    def set_interval(self, *args, **kwargs) -> None:
        return None

    def push_screen(self, screen) -> None:
        self.last_screen = screen

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


def test_app_permissions_updates_runner_and_session(tmp_path: Path, monkeypatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]

    app.on_input_submitted(type("Submitted", (), {"value": "/permissions read_only"})())

    assert app.session.permission_mode == "read_only"
    assert app.runner.config.permission_mode == "read_only"
    assert app.runner.session.permission_mode == "read_only"


def test_app_move_updates_project_context_and_runner(tmp_path: Path, monkeypatch) -> None:
    _install_fake_textual(monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    app = create_tui_agent_app(project=tmp_path)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]

    app.on_input_submitted(SimpleNamespace(value="/move other"))

    assert app._project_context.resolved_project == other.resolve()
    assert app.runner.project.resolved_project == other.resolve()
    assert app.session.project_path == other.resolve()
    assert other.resolve().name in widgets["#top-status"].text


def test_app_copy_to_clipboard_writes_system_clipboard(tmp_path: Path, monkeypatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
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
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
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
