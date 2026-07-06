from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.pr_assist.manifest_loader import (
    build_safety_gate,
    load_artifact_manifest,
    validate_artifact_manifest,
    validate_required_artifacts,
)
from codepilot.pr_assist.models import ManifestInvalidError
from codepilot.repo.git_utils import sha256_file


def _write_run_dir(tmp_path: Path, *, safety_decision: str = "allow", status: str = "success") -> Path:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    contents = {
        "issue.json": "{}",
        "report.md": "# report\n",
        "report.json": json.dumps({"run_id": "issue-test", "tests": {"status": "passed"}, "changed_files": ["src/calc.py"]}),
        "changes.patch": "diff --git a/src/calc.py b/src/calc.py\n",
        "pr_summary.md": "# summary\n",
        "restore_plan.md": "# restore\n",
    }
    for name, content in contents.items():
        (run_dir / name).write_text(content, encoding="utf-8")
    artifacts = []
    for name, filename in [
        ("issue_json", "issue.json"),
        ("report_md", "report.md"),
        ("report_json", "report.json"),
        ("patch", "changes.patch"),
        ("pr_summary", "pr_summary.md"),
        ("restore_plan", "restore_plan.md"),
        ("artifact_manifest", "artifact_manifest.json"),
    ]:
        path = run_dir / filename
        artifacts.append(
            {
                "name": name,
                "path": filename,
                "kind": name,
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "sha256": None if name == "artifact_manifest" else sha256_file(path),
            }
        )
    manifest = {
        "schema_version": "codepilot.artifact_manifest.v1",
        "run_id": "issue-test",
        "status": status,
        "success": safety_decision == "allow",
        "repo_path": str(tmp_path / "repo"),
        "effective_repo_path": str(tmp_path / "repo"),
        "used_worktree": False,
        "safety_decision": safety_decision,
        "safety_reason": None,
        "safety_warnings": [],
        "safety_summary": {"baseline_dirty": False, "protected_after_files": []},
        "patch": {"changed_files": ["src/calc.py"], "is_empty": False, "sha256": sha256_file(run_dir / "changes.patch")},
        "artifacts": artifacts,
    }
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run_dir


def test_load_artifact_manifest_reads_json(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)

    assert load_artifact_manifest(run_dir / "artifact_manifest.json")["run_id"] == "issue-test"


def test_validate_artifact_manifest_reports_missing_schema_version(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest.pop("schema_version")

    assert "unsupported schema_version" in validate_artifact_manifest(manifest)


def test_validate_required_artifacts_rejects_parent_escape(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest["artifacts"][0]["path"] = "../evil.json"

    with pytest.raises(ManifestInvalidError):
        validate_required_artifacts(run_dir, manifest)


def test_validate_required_artifacts_rejects_absolute_path(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest["artifacts"][0]["path"] = str((tmp_path / "evil.json").resolve())

    with pytest.raises(ManifestInvalidError):
        validate_required_artifacts(run_dir, manifest)


def test_validate_required_artifacts_reports_missing_file(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    (run_dir / "report.json").unlink()

    assert "artifact missing on disk: report_json" in validate_required_artifacts(run_dir, manifest)


def test_validate_required_artifacts_reports_size_mismatch(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    for item in manifest["artifacts"]:
        if item["name"] == "report_json":
            item["size_bytes"] = 1

    assert "artifact size mismatch: report_json" in validate_required_artifacts(run_dir, manifest)


def test_validate_required_artifacts_reports_sha_mismatch(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    for item in manifest["artifacts"]:
        if item["name"] == "report_json":
            item["sha256"] = "bad"

    assert "artifact sha256 mismatch: report_json" in validate_required_artifacts(run_dir, manifest)


def test_validate_artifact_manifest_reports_token_like_string(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest["repo_path"] = "ghp_abcdefghijklmnopqrstuvwxyz12345"

    assert "token-like string detected in artifact_manifest" in validate_artifact_manifest(manifest)


def test_build_safety_gate_fail_and_warn(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, safety_decision="deny", status="repo_safety_denied")
    fail_manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    warn_manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    warn_manifest["safety_decision"] = "warn"
    warn_manifest["status"] = "success"
    warn_manifest["safety_summary"]["baseline_dirty"] = True

    assert build_safety_gate(fail_manifest).status == "fail"
    assert build_safety_gate(warn_manifest).status == "warn"


def test_validate_required_artifacts_requires_patch_on_safety_pass(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] != "patch"]

    assert "missing artifact entry: patch" in validate_required_artifacts(run_dir, manifest)


def test_validate_required_artifacts_allows_missing_patch_on_repo_safety_denied(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, safety_decision="deny", status="repo_safety_denied")
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] not in {"patch", "report_json"}]
    manifest["patch"] = None

    assert validate_required_artifacts(run_dir, manifest) == []


def test_validate_required_artifacts_allows_artifact_manifest_self_size_mismatch(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    for item in manifest["artifacts"]:
        if item["name"] == "artifact_manifest":
            item["exists"] = True
            item["size_bytes"] = 1
            item["sha256"] = "bad"
    errors = validate_required_artifacts(run_dir, manifest)

    assert "artifact size mismatch: artifact_manifest" not in errors
    assert "artifact sha256 mismatch: artifact_manifest" not in errors


def test_validate_required_artifacts_allows_report_md_without_report_json(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path)
    manifest = load_artifact_manifest(run_dir / "artifact_manifest.json")
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] != "report_json"]
    (run_dir / "report.json").unlink()

    errors = validate_required_artifacts(run_dir, manifest)

    assert "missing artifact entry: report_json" not in errors
    assert "missing report artifact entry: report_json or report_md" not in errors
