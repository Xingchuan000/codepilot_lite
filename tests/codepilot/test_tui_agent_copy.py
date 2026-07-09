from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.tui_agent import app as app_module
from codepilot.tui_agent.app import create_tui_agent_app
from codepilot.tui_agent.layout import format_transcript_plain
from codepilot.tui_agent.models import TranscriptItem


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


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install_fake_textual(monkeypatch)
    app = create_tui_agent_app(project=tmp_path)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    return app, widgets


def test_copy_transcript_copies_plain_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(
            TranscriptItem(id="1", kind="user_message", timestamp="t", body="hello", copy_text="You: hello"),
            TranscriptItem(id="2", kind="assistant_raw", timestamp="t", body="raw"),
        ),
    )

    app.action_copy_transcript()

    assert app.clipboard == format_transcript_plain(app._reducer.view.transcript)


def test_open_copy_screen_uses_full_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(
            TranscriptItem(id="1", kind="user_message", timestamp="t", body="hello", copy_text="You: hello"),
            TranscriptItem(id="2", kind="tool_result", timestamp="t", tool_name="list_files", body="src/main.py", status="success"),
        ),
    )

    app.action_open_copy_screen()

    assert "You: hello" in app.last_screen.text
    assert "✓ list_files" in app.last_screen.text


def test_copy_command_without_history_shows_empty_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)

    app.on_input_submitted(SimpleNamespace(value="/copy"))

    assert app.last_screen.text == "Transcript is empty."


def test_export_transcript_writes_file_and_appends_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, widgets = _app(tmp_path, monkeypatch)
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(TranscriptItem(id="1", kind="system_status", timestamp="t", body="ready"),),
    )

    app.on_input_submitted(SimpleNamespace(value="/export-transcript"))

    transcript_path = app.session.session_dir / "transcript.md"
    assert transcript_path.exists()
    assert transcript_path.read_text(encoding="utf-8") == "• ready"
    assert app._reducer.view.transcript[-1].kind == "command_output"
    assert len(widgets["#transcript"].mounted) == 2
