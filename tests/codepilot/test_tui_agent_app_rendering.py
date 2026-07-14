from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from codepilot.session.database import SessionDatabase
from codepilot.tui_agent import app as app_module
from codepilot.tui_agent.app import create_tui_agent_app
from codepilot.tui_agent.models import TUIEvent, TranscriptItem


class _FakeWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.updated: list[str] = []
        self.value = kwargs.get("value", "")
        self.disabled = kwargs.get("disabled", False)
        self.text = args[0] if args else ""
        self.clear_count = 0

    def update(self, text: str) -> None:
        self.updated.append(text)
        self.text = text

    def clear(self) -> None:
        self.clear_count += 1

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
        self.scroll_end_count = 0
        self.is_vertical_scroll_end = True

    def mount(self, widget) -> None:
        self.mounted.append(widget)

    def scroll_end(self, animate: bool = False) -> None:
        self.scroll_end_count += 1


class _FakeContainer:
    def __enter__(self) -> "_FakeContainer":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeApp:
    def __init__(self, *args, **kwargs) -> None:
        self.last_screen = None
        self.last_screen_callback = None
        self.exited = False
        self.clipboard = ""

    def set_interval(self, *args, **kwargs) -> None:
        return None

    def push_screen(self, screen, callback=None) -> None:
        self.last_screen = screen
        self.last_screen_callback = callback

    def exit(self) -> None:
        self.exited = True

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


def _create_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _install_fake_textual(monkeypatch)
    return create_tui_agent_app(project=tmp_path, session_database=SessionDatabase(tmp_path / "data" / "sessions.sqlite3"))


def test_append_new_transcript_items_is_append_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(
            TranscriptItem(id="1", kind="user_message", timestamp="t", body="hello", copy_text="You: hello"),
            TranscriptItem(id="2", kind="assistant_plan", timestamp="t", body="先检查结构", copy_text="+ Plan: 先检查结构"),
        ),
    )

    app._append_new_transcript_items()
    app._append_new_transcript_items()

    assert len(widgets["#transcript"].mounted) == 2
    assert widgets["#transcript"].scroll_end_count == 2
    assert widgets["#transcript"].clear_count == 0


def test_append_new_transcript_items_does_not_force_bottom_when_user_scrolled_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    widgets["#transcript"].is_vertical_scroll_end = False
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(
            TranscriptItem(id="1", kind="user_message", timestamp="t", body="hello", copy_text="You: hello"),
            TranscriptItem(id="2", kind="assistant_plan", timestamp="t", body="先检查结构", copy_text="+ Plan: 先检查结构"),
        ),
    )

    app._append_new_transcript_items()

    assert len(widgets["#transcript"].mounted) == 2
    assert widgets["#transcript"].scroll_end_count == 0


def test_refresh_does_not_clear_transcript_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(TranscriptItem(id="1", kind="system_status", timestamp="t", body="Run finished: success"),),
    )

    app._refresh()

    assert widgets["#transcript"].clear_count == 0
    assert len(widgets["#transcript"].mounted) == 1


def test_app_css_allocates_transcript_width(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_app(tmp_path, monkeypatch)

    assert "#transcript" in app.__class__.CSS
    assert "width: 1fr;" in app.__class__.CSS
    assert "#side-status" in app.__class__.CSS


def test_status_command_appends_transcript_without_overwriting_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._create_new_session()
    app._reducer.view = replace(
        app._reducer.view,
        transcript=(TranscriptItem(id="1", kind="system_status", timestamp="t", body="first"),),
    )

    app.on_input_submitted(SimpleNamespace(value="/status"))

    assert len(app._reducer.view.transcript) == 2
    assert app._reducer.view.transcript[-1].kind == "command_output"
    assert len(widgets["#transcript"].mounted) == 2


def test_user_message_is_rendered_only_after_committed_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _create_app(tmp_path, monkeypatch)
    widgets = _widgets()
    app.query_one = lambda selector, _type=None: widgets[selector]  # type: ignore[method-assign]
    app._create_new_session()
    app.runner.start_task = lambda text: "run-1"  # type: ignore[method-assign]
    app.runner.is_running = lambda: False  # type: ignore[method-assign]

    app.on_input_submitted(SimpleNamespace(value="请列出项目结构"))

    assert app._reducer.view.transcript == ()
    app._event_stream.publish(TUIEvent(type="user_message", timestamp="t", payload={"text": "请列出项目结构"}))
    app._drain_events()
    assert app._reducer.view.transcript[0].kind == "user_message"
    assert widgets["#transcript"].mounted[0].text.startswith("You: ")
