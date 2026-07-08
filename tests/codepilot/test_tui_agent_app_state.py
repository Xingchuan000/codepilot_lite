from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from codepilot.tui_agent import app as app_module
from codepilot.tui_agent.app import create_tui_agent_app


class _FakeWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.lines: list[str] = []
        self.value = kwargs.get("value", "")

    def update(self, text: str) -> None:
        self.text = text

    def write(self, text: str) -> None:
        self.lines.append(text)

    def clear(self) -> None:
        self.lines.clear()

    def selection_updated(self, selection) -> None:
        return None


class _FakeContainer:
    def __enter__(self) -> "_FakeContainer":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeApp:
    def __init__(self, *args, **kwargs) -> None:
        self.last_screen = None
        self.exited = False
        self.clipboard = ""

    def set_interval(self, *args, **kwargs) -> None:
        return None

    def push_screen(self, screen) -> None:
        self.last_screen = screen

    def exit(self) -> None:
        self.exited = True

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard = text


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
            _FakeWidget,
            _FakeWidget,
            _FakeWidget,
            _FakeWidget,
            _FakeWidget,
            _FakeModalScreen,
            _FakeBinding,
        ),
    )


class _MainLog:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.clear_count = 0

    def write(self, text: str) -> None:
        self.lines.append(text)

    def update(self, text: str) -> None:
        self.lines.append(text)

    def clear(self) -> None:
        self.clear_count += 1


class _TaskInput:
    def __init__(self) -> None:
        self.value = "task"


class _StaticWidget:
    def __init__(self) -> None:
        self.updated: list[str] = []
        self.clear_count = 0

    def update(self, text: str) -> None:
        self.updated.append(text)

    def write(self, text: str) -> None:
        self.updated.append(text)

    def clear(self) -> None:
        self.clear_count += 1


def test_app_status_does_not_use_unbound_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    main_log = _MainLog()
    task_input = _TaskInput()

    app.query_one = lambda selector, _type=None: main_log if selector == "#main-log" else task_input  # type: ignore[method-assign]
    app._refresh = lambda: None  # type: ignore[method-assign]

    app.on_input_submitted(SimpleNamespace(value="/status"))

    assert main_log.lines


def test_app_help_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    main_log = _MainLog()
    task_input = _TaskInput()

    app.query_one = lambda selector, _type=None: main_log if selector == "#main-log" else task_input  # type: ignore[method-assign]
    app._refresh = lambda: None  # type: ignore[method-assign]

    app.on_input_submitted(SimpleNamespace(value="/help"))

    assert any("/help" in line for line in main_log.lines)


def test_app_refresh_does_not_clear_unchanged_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    main_log = _MainLog()
    timeline = _MainLog()
    header = _StaticWidget()
    result = _StaticWidget()

    app.query_one = lambda selector, _type=None: {  # type: ignore[method-assign]
        "#main-log": main_log,
        "#timeline": timeline,
        "#header": header,
        "#result": result,
    }[selector]

    app._refresh()
    app._refresh()

    assert main_log.clear_count == 1
    assert timeline.clear_count == 1
    assert len(header.updated) == 1
    assert len(result.updated) == 1


def test_app_permissions_updates_runner_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    main_log = _MainLog()
    task_input = _TaskInput()

    app.query_one = lambda selector, _type=None: main_log if selector == "#main-log" else task_input  # type: ignore[method-assign]
    app._refresh = lambda: None  # type: ignore[method-assign]

    app.on_input_submitted(SimpleNamespace(value="/permissions read_only"))

    assert app.session.permission_mode == "read_only"
    assert app.runner.config.permission_mode == "read_only"
    assert app.runner.session.permission_mode == "read_only"
    assert any("read_only" in line for line in main_log.lines)


def test_app_copy_to_clipboard_writes_system_clipboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr("codepilot.tui_agent.app.sys.platform", "darwin")
    monkeypatch.setattr("codepilot.tui_agent.app.shutil.which", lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else None)
    monkeypatch.setattr(
        "codepilot.tui_agent.app.subprocess.run",
        lambda cmd, input=None, text=None, check=None: calls.append((cmd, input or "")),
    )

    app.copy_to_clipboard("hello")

    assert app.clipboard == "hello"
    assert calls == [(["pbcopy"], "hello")]
