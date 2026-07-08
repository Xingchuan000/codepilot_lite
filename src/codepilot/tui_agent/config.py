from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codepilot.tui_agent.models import PermissionMode, ProjectContext


@dataclass(frozen=True)
class TUIAgentConfig:
    model: str | None = None
    permission_mode: PermissionMode = "manual"
    runs_dir: Path | None = None
    mcp_config: Path | None = None
    default_test_command: str | None = None
    max_steps: int = 12
    auto_report: bool = True
    source: dict[str, str] = field(default_factory=dict)


def load_project_config(project: ProjectContext) -> tuple[dict[str, Any], list[str]]:
    path = project.project_config_path
    if path is None or not path.exists():
        return {}, []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project config must be a JSON object")
    warnings = [f"unknown_field:{key}" for key in data if key not in {"model", "permission_mode", "runs_dir", "mcp_config", "default_test_command", "max_steps", "auto_report"}]
    return data, warnings


def merge_config(
    *,
    cli_model: str | None,
    cli_permission_mode: PermissionMode | None,
    cli_runs_dir: str | Path | None,
    cli_mcp_config: str | Path | None,
    cli_max_steps: int | None,
    project: ProjectContext,
) -> TUIAgentConfig:
    project_config, warnings = load_project_config(project)
    permission_mode = cli_permission_mode or project_config.get("permission_mode") or "manual"
    if permission_mode not in {"manual", "read_only", "accept_edits", "unsafe_auto"}:
        raise ValueError("invalid permission_mode")
    runs_dir = Path(cli_runs_dir or project_config.get("runs_dir") or project.default_runs_dir)
    if not runs_dir.is_absolute():
        runs_dir = (project.workspace_root / runs_dir).resolve()
    mcp_config = cli_mcp_config or project_config.get("mcp_config") or project.mcp_config_path
    if mcp_config is not None:
        mcp_path = Path(mcp_config)
        if not mcp_path.is_absolute():
            mcp_path = (project.workspace_root / mcp_path).resolve()
    else:
        mcp_path = None
    project_max_steps = project_config.get("max_steps")
    return TUIAgentConfig(
        model=cli_model or project_config.get("model"),
        permission_mode=permission_mode,
        runs_dir=runs_dir,
        mcp_config=mcp_path,
        default_test_command=project_config.get("default_test_command"),
        max_steps=cli_max_steps if cli_max_steps is not None else int(project_max_steps) if project_max_steps is not None else 12,
        auto_report=bool(project_config.get("auto_report", True)),
        source={
            "model": "cli" if cli_model is not None else ("project_config" if "model" in project_config else "default"),
            "permission_mode": "cli" if cli_permission_mode is not None else ("project_config" if "permission_mode" in project_config else "default"),
            "runs_dir": "cli" if cli_runs_dir is not None else ("project_config" if "runs_dir" in project_config else "default"),
            "mcp_config": "cli" if cli_mcp_config is not None else ("project_config" if "mcp_config" in project_config else "default"),
            "max_steps": "cli" if cli_max_steps is not None else ("project_config" if "max_steps" in project_config else "default"),
            "auto_report": "project_config" if "auto_report" in project_config else "default",
            "warnings": ",".join(warnings),
        },
    )
