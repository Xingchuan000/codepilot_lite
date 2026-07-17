from __future__ import annotations

import json
from pathlib import Path

from codepilot.tui_agent.config import merge_config
from codepilot.tui_agent.project_resolver import resolve_project


def test_project_config_applies_when_cli_values_are_none(tmp_path: Path) -> None:
    config_dir = tmp_path / ".codepilot"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"permission_mode": "read_only", "max_steps": 3}), encoding="utf-8")

    config = merge_config(
        cli_model=None,
        cli_permission_mode=None,
        cli_mcp_config=None,
        cli_max_steps=None,
        project=resolve_project(tmp_path),
    )

    assert config.permission_mode == "read_only"
    assert config.max_steps == 3


def test_cli_values_override_project_config(tmp_path: Path) -> None:
    config_dir = tmp_path / ".codepilot"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"permission_mode": "read_only", "max_steps": 3}), encoding="utf-8")

    config = merge_config(
        cli_model=None,
        cli_permission_mode="accept_edits",
        cli_mcp_config=None,
        cli_max_steps=7,
        project=resolve_project(tmp_path),
    )

    assert config.permission_mode == "accept_edits"
    assert config.max_steps == 7


def test_project_config_preserves_zero_max_steps(tmp_path: Path) -> None:
    config_dir = tmp_path / ".codepilot"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"max_steps": 0}), encoding="utf-8")

    config = merge_config(
        cli_model=None,
        cli_permission_mode=None,
        cli_mcp_config=None,
        cli_max_steps=None,
        project=resolve_project(tmp_path),
    )

    assert config.max_steps == 0
