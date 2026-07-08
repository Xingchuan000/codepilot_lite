from __future__ import annotations

from pathlib import Path

from codepilot.tui_agent.models import ProjectContext


def model_label(model: str | None) -> str:
    return model or "default"


def mcp_label(mcp_config_path: Path | None) -> str:
    return str(mcp_config_path) if mcp_config_path is not None else "none"


def format_project_status(context: ProjectContext) -> str:
    lines = [
        f"Project: {context.resolved_project}",
        f"Git: {context.git_root or 'non-git'} ({context.git_dirty_status})",
        f"Workspace: {context.workspace_root}",
        f"Runs: {context.default_runs_dir}",
        f"MCP: {mcp_label(context.mcp_config_path)}",
    ]
    if context.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in context.warnings)
    return "\n".join(lines)

