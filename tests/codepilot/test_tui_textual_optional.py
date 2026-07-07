from __future__ import annotations

import importlib
import sys
from pathlib import Path

from typer.testing import CliRunner


def test_codepilot_cli_imports_when_textual_is_stubbed(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "textual", None)
    sys.modules.pop("codepilot.cli", None)

    module = importlib.import_module("codepilot.cli")

    assert module.app is not None


def test_dashboard_static_and_json_work_without_textual_module(tmp_path: Path) -> None:
    from codepilot.cli import app
    from tests.codepilot.tui_helpers import make_success_run

    runner = CliRunner()
    make_success_run(tmp_path)

    static_result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--static"])
    json_result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--json"])

    assert static_result.exit_code == 0
    assert json_result.exit_code == 0


def test_dashboard_tui_returns_readable_error_when_textual_missing(monkeypatch, tmp_path: Path) -> None:
    from codepilot.cli import app
    from tests.codepilot.tui_helpers import make_success_run

    runner = CliRunner()
    make_success_run(tmp_path)
    import codepilot.tui.app as app_module

    monkeypatch.setattr(app_module, "_load_textual", lambda: (_ for _ in ()).throw(RuntimeError("Textual is not installed. Use --static or install optional dependency.")))

    result = runner.invoke(app, ["dashboard", "--runs-dir", str(tmp_path), "--tui"])

    assert result.exit_code != 0
    assert "Textual is not installed" in result.stderr


def test_tui_module_top_level_does_not_import_textual() -> None:
    module = importlib.import_module("codepilot.tui.app")

    assert "textual" not in module.__dict__
