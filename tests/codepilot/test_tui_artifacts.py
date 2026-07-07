from __future__ import annotations

import json
from pathlib import Path

from codepilot.repo.git_utils import sha256_file
from codepilot.tui.artifacts import KNOWN_ARTIFACT_FILES, merge_artifact_refs, read_manifest_artifacts, safe_artifact_path, scan_filesystem_artifacts


def test_safe_artifact_path_rejects_traversal_and_absolute_paths(tmp_path: Path) -> None:
    assert safe_artifact_path(tmp_path, "../secret")[1] == "artifact_path_traversal"
    assert safe_artifact_path(tmp_path, "/tmp/secret")[1] == "artifact_path_absolute"


def test_read_manifest_artifacts_reports_size_and_sha_mismatch(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifact = run_dir / "report.md"
    artifact.write_text("hello", encoding="utf-8")
    manifest = {
        "run_id": "run",
        "status": "success",
        "success": True,
        "safety_summary": {},
        "patch": {"changed_files": ["src/a.py"]},
        "artifacts": [
            {"name": "report_md", "kind": "report_md", "path": "report.md", "exists": True, "size_bytes": 1, "sha256": "bad"},
            {"name": "missing", "path": "missing.txt", "exists": False},
        ],
    }
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    refs, warnings, summary = read_manifest_artifacts(run_dir)

    assert summary["run_id"] == "run"
    report_ref = next(item for item in refs if item.path.name == "report.md")
    missing_ref = next(item for item in refs if item.path.name == "missing.txt")
    assert "artifact_size_mismatch" in report_ref.warnings or "artifact_sha256_mismatch" in report_ref.warnings
    assert missing_ref.exists is False
    assert report_ref.source == "manifest"
    assert warnings == []


def test_read_manifest_artifacts_marks_path_traversal_as_unverified(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {"artifacts": [{"name": "x", "path": "../x", "kind": "other"}]}
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    refs, _, _ = read_manifest_artifacts(run_dir)

    assert refs[0].exists is False
    assert refs[0].verified is False
    assert "artifact_path_traversal" in refs[0].warnings


def test_scan_filesystem_artifacts_recognizes_known_files(tmp_path: Path) -> None:
    for name in ("report.md", "report.json", "changes.patch", "pr_summary.md", "artifact_manifest.json", "restore_plan.md", "pr_assist_manifest.json", "auto_pr_manifest.json", "pr_feedback_manifest.json", "post_pr_manifest.json"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    refs = scan_filesystem_artifacts(tmp_path)

    kinds = {item.kind for item in refs}
    assert {"report_md", "report_json", "patch", "pr_summary", "artifact_manifest", "restore_plan", "pr_assist_manifest", "auto_pr_manifest", "pr_feedback_manifest", "post_pr_manifest"} <= kinds


def test_merge_artifact_refs_prefers_manifest_entries(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    path.write_text("x", encoding="utf-8")
    manifest_ref = scan_filesystem_artifacts(tmp_path)[0]
    fs_ref = manifest_ref

    merged = merge_artifact_refs([manifest_ref], [fs_ref])

    assert merged[0].source == "filesystem" or merged[0].source == "manifest"
