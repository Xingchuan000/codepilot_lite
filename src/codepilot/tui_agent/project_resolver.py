from __future__ import annotations

import subprocess
from pathlib import Path

from codepilot.tui_agent import TUI_AGENT_SCHEMA_VERSION
from codepilot.tui_agent.models import ProjectContext


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=False, shell=False, capture_output=True, text=True)


def _find_git_root(path: Path) -> tuple[Path | None, list[str]]:
    try:
        result = _run_git(["git", "rev-parse", "--show-toplevel"], cwd=path)
    except FileNotFoundError:
        return None, ["git_not_found"]
    if result.returncode != 0:
        return None, []
    root = result.stdout.strip()
    return (Path(root).resolve(), []) if root else (None, [])


def _git_dirty_status(path: Path) -> tuple[str, list[str]]:
    try:
        result = _run_git(["git", "status", "--porcelain"], cwd=path)
    except FileNotFoundError:
        return "unknown", ["git_not_found"]
    if result.returncode != 0:
        return "unknown", ["git_status_failed"]
    return ("clean", []) if not result.stdout.strip() else ("dirty", [])


def resolve_project(project: str | Path | None = None) -> ProjectContext:
    project_path = Path.cwd() if project is None else Path(project)
    resolved_project = project_path.expanduser().resolve()
    if not resolved_project.exists():
        raise FileNotFoundError(f"project path does not exist: {resolved_project}")
    if not resolved_project.is_dir():
        raise NotADirectoryError("project path must be a directory")

    git_root, warnings = _find_git_root(resolved_project)
    is_git_repo = git_root is not None
    git_dirty_status = "non-git"
    if is_git_repo:
        git_dirty_status, dirty_warnings = _git_dirty_status(git_root or resolved_project)
        warnings.extend(dirty_warnings)

    workspace_root = git_root or resolved_project
    project_config_path = workspace_root / ".codepilot" / "config.json"
    mcp_config_path = workspace_root / ".mcp.json"
    if project_config_path.exists():
        try:
            import json

            data = json.loads(project_config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
            warnings.append("project_config_unreadable")
        if isinstance(data, dict):
            mcp_value = data.get("mcp_config")
            if isinstance(mcp_value, str) and mcp_value.strip():
                mcp_config_path = (workspace_root / mcp_value).expanduser().resolve()

    instructions_files = tuple(
        path for path in (workspace_root / "AGENTS.md", workspace_root / "CLAUDE.md", workspace_root / "README.md") if path.exists()
    )

    return ProjectContext(
        schema_version=TUI_AGENT_SCHEMA_VERSION,
        project_path=project_path,
        resolved_project=resolved_project,
        git_root=git_root,
        is_git_repo=is_git_repo,
        git_dirty_status=git_dirty_status,
        workspace_root=workspace_root,
        effective_repo_path=workspace_root,
        project_config_path=project_config_path if project_config_path.exists() else None,
        mcp_config_path=mcp_config_path if mcp_config_path.exists() else None,
        instructions_files=instructions_files,
        warnings=tuple(warnings),
    )
