from __future__ import annotations

from pathlib import Path

from codepilot.repo.models import PatchMetadata, RepoSafetyConfig, RepoSafetyResult, RepoStateSnapshot, to_jsonable


def test_repo_safety_config_defaults_are_stable() -> None:
    config = RepoSafetyConfig()

    assert config.dirty_policy == "fail"
    assert config.worktree_mode == "off"
    assert ".env" in config.protected_paths
    assert ".github/workflows/**" in config.protected_paths
    assert "runs/**" in config.protected_paths


def test_repo_safety_config_protected_paths_do_not_share_same_list() -> None:
    first = RepoSafetyConfig()
    second = RepoSafetyConfig()

    assert first.protected_paths is not second.protected_paths


def test_repo_state_snapshot_defaults_files_to_empty_list() -> None:
    snapshot = RepoStateSnapshot(repo_path=Path("/tmp/repo"), head_sha=None, branch=None, is_dirty=False)

    assert snapshot.files == []


def test_repo_safety_result_defaults_warnings_to_empty_list() -> None:
    result = RepoSafetyResult(decision="allow")

    assert result.warnings == []


def test_patch_metadata_defaults_protected_changed_files_to_empty_list() -> None:
    metadata = PatchMetadata(
        patch_path=Path("/tmp/changes.patch"),
        is_empty=True,
        size_bytes=0,
        sha256="hash",
        changed_files=[],
    )

    assert metadata.protected_changed_files == []


def test_to_jsonable_converts_path_and_dataclass() -> None:
    snapshot = RepoStateSnapshot(repo_path=Path("/tmp/repo"), head_sha="abc", branch="main", is_dirty=False)

    assert to_jsonable(snapshot) == {
        "repo_path": "/tmp/repo",
        "head_sha": "abc",
        "branch": "main",
        "is_dirty": False,
        "files": [],
        "untracked_count": 0,
        "protected_dirty_files": [],
    }
