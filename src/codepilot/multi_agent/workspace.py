from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from codepilot.github.patch_exporter import export_patch_with_metadata
from codepilot.repo.worktree import create_issue_worktree, remove_issue_worktree
from codepilot.session.artifacts import ArtifactStore


@dataclass(frozen=True)
class AgentWorkspace:
    path: Path
    run_id: str
    branch: str
    original_repo: Path


def create_agent_worktree(repo: Path) -> AgentWorkspace:
    run_id = f"agent-{uuid4().hex}"
    result = create_issue_worktree(
        repo,
        run_id=run_id,
        branch_prefix="codepilot-agent",
    )
    return AgentWorkspace(
        path=result.worktree_path,
        run_id=run_id,
        branch=result.branch_name,
        original_repo=result.original_repo_path,
    )


def persist_agent_patch(
    artifacts: ArtifactStore,
    child_session_id: str,
    workspace: Path,
) -> str:
    tmp = artifacts.paths.sessions_dir / child_session_id / "agent-output.patch"
    patch_path, _ = export_patch_with_metadata(workspace, tmp)
    patch_text = patch_path.read_text(encoding="utf-8")
    if patch_text and not patch_text.endswith("\n"):
        patch_text += "\n"
    artifact = artifacts.put_text(child_session_id, "subagent_patch", patch_text, mime_type="text/x-diff")
    return artifact.artifact_id


def discard_agent_worktree(workspace: AgentWorkspace) -> None:
    remove_issue_worktree(
        workspace.path,
        original_repo=workspace.original_repo,
        branch_name=workspace.branch,
        force=True,
    )
