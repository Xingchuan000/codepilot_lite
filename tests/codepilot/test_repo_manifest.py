from __future__ import annotations

import json
from pathlib import Path

from codepilot.repo.manifest import (
    build_artifact_entry,
    build_artifact_manifest,
    write_artifact_manifest,
    write_artifact_manifest_two_phase,
)
from codepilot.repo.models import PatchMetadata, RepoSafetyResult, RepoStateSnapshot, to_jsonable


def test_build_artifact_entry_records_size_and_sha(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    path = run_dir / "issue.json"
    path.write_text("{}", encoding="utf-8")

    entry = build_artifact_entry(name="issue_json", path=path, kind="issue_json", run_dir=run_dir)

    assert entry.exists is True
    assert entry.size_bytes == 2
    assert entry.sha256 is not None


def test_build_artifact_entry_for_missing_file(tmp_path: Path) -> None:
    entry = build_artifact_entry(name="missing", path=tmp_path / "missing.txt", kind="missing", run_dir=tmp_path)

    assert entry.exists is False


def test_write_artifact_manifest_writes_expected_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    for name in (
        "issue.json",
        "trace.jsonl",
        "report.md",
        "report.json",
        "changes.patch",
        "pr_summary.md",
        "restore_plan.md",
    ):
        (run_dir / name).write_text("artifact", encoding="utf-8")
    before = RepoStateSnapshot(repo_path=tmp_path / "repo", head_sha="abc", branch="main", is_dirty=False)
    metadata = PatchMetadata(
        patch_path=run_dir / "changes.patch",
        is_empty=False,
        size_bytes=(run_dir / "changes.patch").stat().st_size,
        sha256="hash",
        changed_files=["src/calc.py"],
        generated_from_repo=tmp_path / "repo",
    )
    manifest = build_artifact_manifest(
        run_id="issue-test",
        run_dir=run_dir,
        status="success",
        success=True,
        repo_path=tmp_path / "repo",
        effective_repo_path=tmp_path / "repo",
        used_worktree=False,
        worktree_path=None,
        safety_result=RepoSafetyResult(decision="allow"),
        before=before,
        after=before,
        patch_metadata=metadata,
        artifact_paths={
            "issue_json": run_dir / "issue.json",
            "trace": run_dir / "trace.jsonl",
            "report_md": run_dir / "report.md",
            "report_json": run_dir / "report.json",
            "patch": run_dir / "changes.patch",
            "pr_summary": run_dir / "pr_summary.md",
            "restore_plan": run_dir / "restore_plan.md",
            "artifact_manifest": run_dir / "artifact_manifest.json",
        },
    )

    path = write_artifact_manifest_two_phase(manifest, run_dir / "artifact_manifest.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "codepilot.artifact_manifest.v1"
    assert payload["created_at"]
    assert payload["generator"] == "codepilot"
    assert payload["generator_version"]
    assert {item["path"] for item in payload["artifacts"]} >= {
        "issue.json",
        "trace.jsonl",
        "report.md",
        "report.json",
        "changes.patch",
        "pr_summary.md",
        "restore_plan.md",
        "artifact_manifest.json",
    }
    assert payload["safety_decision"] == "allow"
    assert payload["patch"]["sha256"] == "hash"
    assert payload["patch"]["changed_files"] == ["src/calc.py"]
    artifacts = {item["name"]: item for item in payload["artifacts"]}
    assert artifacts["artifact_manifest"]["exists"] is True
    dumped = json.dumps(payload)
    assert "OPENAI_API_KEY" not in dumped
    assert "GITHUB_TOKEN" not in dumped
    assert "ANTHROPIC_API_KEY" not in dumped
    assert "ghp_test_secret" not in dumped


def test_manifest_redacts_absolute_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    manifest = build_artifact_manifest(
        run_id="issue-test",
        run_dir=run_dir,
        status="success",
        success=True,
        repo_path=tmp_path / "repo",
        effective_repo_path=tmp_path / "repo",
        used_worktree=False,
        worktree_path=None,
        safety_result=RepoSafetyResult(decision="allow"),
        before=None,
        after=None,
        artifact_paths={},
        redact_absolute_paths=True,
    )

    assert manifest.repo_path == "[REDACTED_PATH]"


def test_manifest_redacts_patch_generated_from_repo_when_redact_absolute_paths_enabled(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    manifest = build_artifact_manifest(
        run_id="issue-test",
        run_dir=run_dir,
        status="success",
        success=True,
        repo_path=tmp_path / "repo",
        effective_repo_path=tmp_path / "repo",
        used_worktree=False,
        worktree_path=None,
        safety_result=RepoSafetyResult(decision="allow"),
        before=None,
        after=None,
        patch_metadata=PatchMetadata(
            patch_path=run_dir / "changes.patch",
            is_empty=False,
            size_bytes=1,
            sha256="hash",
            changed_files=["src/calc.py"],
            generated_from_repo=tmp_path / "repo",
        ),
        redact_absolute_paths=True,
    )

    payload = json.loads(json.dumps(to_jsonable(manifest)))
    assert payload["repo_path"] == "[REDACTED_PATH]"
    assert payload["effective_repo_path"] == "[REDACTED_PATH]"
    assert payload["patch"]["generated_from_repo"] == "[REDACTED_PATH]"
    assert str(tmp_path) not in json.dumps(payload)
