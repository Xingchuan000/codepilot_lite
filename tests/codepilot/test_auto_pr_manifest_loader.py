from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.auto_pr.manifest_loader import (
    load_pr_assist_manifest,
    validate_pr_assist_manifest,
)
from codepilot.repo.git_utils import sha256_file


def _write_auto_pr_run_dir(tmp_path: Path, *, patch_empty: bool = False) -> Path:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    (run_dir / "issue.json").write_text(
        json.dumps(
            {
                "title": "Fix add bug",
                "body": "body",
                "number": 123,
                "ref": {"source": "github", "url": "https://github.com/o/r/issues/123", "number": 123},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "pr_body.md").write_text("# Fix add bug\n\nsummary\n", encoding="utf-8")
    (run_dir / "manual_pr_commands.md").write_text("# manual\n", encoding="utf-8")
    (run_dir / "review_checklist.md").write_text("# checklist\n", encoding="utf-8")
    (run_dir / "changes.patch").write_text("" if patch_empty else "diff --git a/src/a.py b/src/a.py\n", encoding="utf-8")
    (run_dir / "pr_summary.md").write_text("# summary\n", encoding="utf-8")
    (run_dir / "restore_plan.md").write_text("# restore\n", encoding="utf-8")
    artifact_manifest = {
        "schema_version": "codepilot.artifact_manifest.v1",
        "run_id": "issue-test",
        "status": "success",
        "success": True,
        "repo_path": "/repo",
        "effective_repo_path": "/repo",
        "used_worktree": False,
        "safety_decision": "allow",
        "safety_summary": {"baseline_dirty": False, "protected_after_files": []},
        "patch": {
            "changed_files": ["src/a.py"],
            "is_empty": patch_empty,
            "sha256": sha256_file(run_dir / "changes.patch"),
            "protected_changed_files": [],
        },
        "artifacts": [],
    }
    for name, filename in [
        ("issue_json", "issue.json"),
        ("patch", "changes.patch"),
        ("pr_summary", "pr_summary.md"),
        ("restore_plan", "restore_plan.md"),
        ("artifact_manifest", "artifact_manifest.json"),
    ]:
        path = run_dir / filename
        artifact_manifest["artifacts"].append(
            {
                "name": name,
                "path": filename,
                "exists": True,
                "size_bytes": path.stat().st_size if path.exists() else None,
                "sha256": None if name == "artifact_manifest" else sha256_file(path),
            }
        )
    (run_dir / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")
    pr_assist_manifest = {
        "schema_version": "codepilot.pr_assist_manifest.v1",
        "run_id": "issue-test",
        "source_artifact_manifest": "artifact_manifest.json",
        "source_artifact_manifest_sha256": sha256_file(run_dir / "artifact_manifest.json"),
        "safety_gate": {"status": "pass", "reasons": [], "warnings": []},
        "generated_artifacts": [
            {
                "name": "pr_body",
                "path": "pr_body.md",
                "exists": True,
                "size_bytes": (run_dir / "pr_body.md").stat().st_size,
                "sha256": sha256_file(run_dir / "pr_body.md"),
            },
            {
                "name": "manual_pr_commands",
                "path": "manual_pr_commands.md",
                "exists": True,
                "size_bytes": (run_dir / "manual_pr_commands.md").stat().st_size,
                "sha256": sha256_file(run_dir / "manual_pr_commands.md"),
            },
            {
                "name": "review_checklist",
                "path": "review_checklist.md",
                "exists": True,
                "size_bytes": (run_dir / "review_checklist.md").stat().st_size,
                "sha256": sha256_file(run_dir / "review_checklist.md"),
            },
            {
                "name": "pr_assist_manifest",
                "path": "pr_assist_manifest.json",
                "exists": True,
                "size_bytes": None,
                "sha256": None,
            },
        ],
        "side_effects": {
            "branch_prepared": True,
            "commit_prepared": True,
            "push_executed": False,
            "pr_created": False,
            "github_api_called": False,
        },
        "branch_name": "codepilot/issue-test",
        "commit_sha": "abc123",
        "warnings": [],
    }
    (run_dir / "pr_assist_manifest.json").write_text(json.dumps(pr_assist_manifest, indent=2), encoding="utf-8")
    return run_dir


def test_load_pr_assist_manifest_reads_valid_manifest(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)

    assert load_pr_assist_manifest(run_dir / "pr_assist_manifest.json")["run_id"] == "issue-test"


def test_load_pr_assist_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pr_assist_manifest(tmp_path / "missing.json")


def test_validate_pr_assist_manifest_schema_error(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["schema_version"] = "bad"

    assert "unsupported schema_version" in validate_pr_assist_manifest(payload, run_dir)


def test_validate_pr_assist_manifest_source_hash_mismatch(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["source_artifact_manifest_sha256"] = "bad"

    assert any("sha256 mismatch" in item for item in validate_pr_assist_manifest(payload, run_dir))


def test_validate_pr_assist_manifest_missing_pr_body(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    (run_dir / "pr_body.md").unlink()

    assert any("pr_body" in item for item in validate_pr_assist_manifest(payload, run_dir))


def test_validate_pr_assist_manifest_pr_body_hash_mismatch(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    (run_dir / "pr_body.md").write_text("# changed\n", encoding="utf-8")

    assert any("sha256 mismatch" in item for item in validate_pr_assist_manifest(payload, run_dir))


def test_validate_pr_assist_manifest_escape_path_rejected(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["generated_artifacts"][0]["path"] = "../escape.md"

    assert any("escapes run_dir" in item for item in validate_pr_assist_manifest(payload, run_dir))


def test_validate_pr_assist_manifest_token_in_manifest_rejected(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["warnings"] = ["GITHUB_TOKEN"]

    assert "token-like string detected in pr_assist_manifest" in validate_pr_assist_manifest(payload, run_dir)


def test_validate_pr_assist_manifest_token_in_pr_body_rejected(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    (run_dir / "pr_body.md").write_text("# title\n\ngithub_pat_example_token_value_12345678901234567890\n", encoding="utf-8")

    assert "token-like string detected in pr_body.md" in validate_pr_assist_manifest(payload, run_dir)


@pytest.mark.parametrize(
    ("field",),
    [("push_executed",), ("pr_created",), ("github_api_called",)],
)
def test_validate_pr_assist_manifest_side_effect_flags_must_be_false(tmp_path: Path, field: str) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["side_effects"][field] = True

    assert any(field in item for item in validate_pr_assist_manifest(payload, run_dir))


def test_validate_pr_assist_manifest_patch_metadata_missing(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    source = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    source["patch"] = None
    (run_dir / "artifact_manifest.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["source_artifact_manifest_sha256"] = sha256_file(run_dir / "artifact_manifest.json")

    assert "missing patch metadata" in validate_pr_assist_manifest(payload, run_dir)


def test_validate_pr_assist_manifest_empty_patch_rejected_by_default(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path, patch_empty=True)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))

    assert "empty patch is not allowed" in validate_pr_assist_manifest(payload, run_dir)


def test_validate_pr_assist_manifest_empty_patch_allowed_when_requested(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path, patch_empty=True)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))

    assert "empty patch is not allowed" not in validate_pr_assist_manifest(payload, run_dir, allow_empty_pr=True)


def test_validate_pr_assist_manifest_commit_sha_missing(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["commit_sha"] = None

    assert "missing commit_sha" in validate_pr_assist_manifest(payload, run_dir)


def test_validate_pr_assist_manifest_branch_name_missing(tmp_path: Path) -> None:
    run_dir = _write_auto_pr_run_dir(tmp_path)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    payload["branch_name"] = None

    assert "missing branch_name" in validate_pr_assist_manifest(payload, run_dir)
