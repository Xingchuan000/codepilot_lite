from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codepilot.pr_assist.workflow import run_pr_assist
from codepilot.repo.git_utils import run_git, sha256_file


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo


def _write_run_dir(
    tmp_path: Path,
    *,
    repo: Path,
    safety_decision: str = "allow",
    status: str = "success",
    redact_paths: bool = False,
) -> Path:
    run_dir = tmp_path / "runs" / "issue-test"
    run_dir.mkdir(parents=True)
    (run_dir / "issue.json").write_text(
        json.dumps({"title": "Fix add bug", "body": "body", "ref": {"source": "file", "file_path": "issue.md"}}),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text("# report\n", encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "issue-test",
                "final_summary": "Implemented fix.",
                "changed_files": ["src/calc.py"],
                "tests": {"status": "passed", "command": "pytest -q", "summary": "1 passed"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "changes.patch").write_text("diff --git a/src/calc.py b/src/calc.py\n", encoding="utf-8")
    (run_dir / "pr_summary.md").write_text("# summary\n", encoding="utf-8")
    (run_dir / "restore_plan.md").write_text("# restore\n", encoding="utf-8")
    repo_path = "[REDACTED_PATH]" if redact_paths else str(repo)
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
        "repo_path": repo_path,
        "effective_repo_path": repo_path,
        "used_worktree": False,
        "safety_decision": safety_decision,
        "safety_reason": "blocked" if safety_decision == "deny" else None,
        "safety_warnings": [],
        "safety_summary": {"baseline_dirty": False, "protected_after_files": []},
        "patch": {"changed_files": ["src/calc.py"], "is_empty": False, "sha256": sha256_file(run_dir / "changes.patch")},
        "artifacts": artifacts,
    }
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return run_dir


def test_run_pr_assist_generates_artifacts_and_manifest(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_pr_assist(run_dir=run_dir, overwrite=True)
    manifest = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))

    assert result.status == "generated"
    assert (run_dir / "pr_body.md").exists()
    assert (run_dir / "manual_pr_commands.md").exists()
    assert (run_dir / "review_checklist.md").exists()
    assert (run_dir / "github_action_template.yml").exists()
    assert manifest["source_artifact_manifest_sha256"] == sha256_file(run_dir / "artifact_manifest.json")
    assert manifest["side_effects"]["push_executed"] is False
    assert manifest["side_effects"]["pr_created"] is False
    assert manifest["side_effects"]["github_api_called"] is False


def test_run_pr_assist_blocks_side_effects_on_safety_fail(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_decision="deny", status="repo_safety_denied")

    result = run_pr_assist(run_dir=run_dir, strict_safety=True, prepare_branch=True, commit=True, overwrite=True)

    assert result.status == "blocked_by_safety"
    assert "apply --check" not in (run_dir / "manual_pr_commands.md").read_text(encoding="utf-8")
    assert "commit -m" not in (run_dir / "manual_pr_commands.md").read_text(encoding="utf-8")


def test_run_pr_assist_manifest_invalid_only_writes_minimal_manifest(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest.pop("schema_version")
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = run_pr_assist(run_dir=run_dir, overwrite=True)

    assert result.status == "manifest_invalid"
    assert result.pr_body_path is None
    assert (run_dir / "pr_assist_manifest.json").exists()


def test_run_pr_assist_overwrite_behavior(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    (run_dir / "pr_body.md").write_text("old\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_pr_assist(run_dir=run_dir, overwrite=False)

    run_pr_assist(run_dir=run_dir, overwrite=True, generate_github_action_template=False)
    assert (run_dir / "report.json").exists()
    assert (run_dir / "changes.patch").exists()
    assert not (run_dir / "github_action_template.yml").exists()


def test_run_pr_assist_redacts_absolute_paths_in_manual_commands(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, redact_paths=True)

    run_pr_assist(run_dir=run_dir, overwrite=True, redact_absolute_paths=True)

    content = (run_dir / "manual_pr_commands.md").read_text(encoding="utf-8")
    assert "<repo>" in content
    assert str(tmp_path) not in content


def test_run_pr_assist_prepare_branch_on_clean_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_pr_assist(run_dir=run_dir, overwrite=True, prepare_branch=True)

    assert result.branch_name == "codepilot/issue-test"
    assert result.status == "branch_prepared"


def test_run_pr_assist_prepare_commit_only_stages_changed_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run_dir = _write_run_dir(tmp_path, repo=repo)

    result = run_pr_assist(run_dir=run_dir, overwrite=True, commit=True)

    assert result.commit_sha == run_git(repo, ["rev-parse", "HEAD"])
    assert "runs/" not in "\n".join(run_git(repo, ["show", "--name-only", "--format=", "HEAD"]).splitlines())


def test_run_pr_assist_accepts_manifest_self_size_drift(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        if item["name"] == "artifact_manifest":
            item["exists"] = True
            item["size_bytes"] = 1
            item["sha256"] = None
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = run_pr_assist(run_dir=run_dir, overwrite=True)

    assert result.status == "generated"
    assert (run_dir / "pr_body.md").exists()


def test_run_pr_assist_generates_with_report_md_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] != "report_json"]
    (run_dir / "report.json").unlink()
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = run_pr_assist(run_dir=run_dir, overwrite=True)

    assert result.status == "generated"
    assert (run_dir / "pr_body.md").exists()
    assert "unknown" in (run_dir / "pr_body.md").read_text(encoding="utf-8")


def test_run_pr_assist_no_strict_safety_generates_review_only_without_blocking(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_decision="deny", status="repo_safety_denied")

    result = run_pr_assist(run_dir=run_dir, strict_safety=False, prepare_branch=True, commit=True, overwrite=True)

    assert result.status == "generated"
    text = (run_dir / "manual_pr_commands.md").read_text(encoding="utf-8")
    assert "apply --check" not in text
    assert "commit -m" not in text
    assert result.branch_name is None
    assert result.commit_sha is None


def test_run_pr_assist_commit_skipped_on_safety_warn(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run_dir = _write_run_dir(tmp_path, repo=repo, safety_decision="warn", status="success")
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["safety_summary"]["baseline_dirty"] = True
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = run_git(repo, ["rev-parse", "HEAD"])

    result = run_pr_assist(run_dir=run_dir, overwrite=True, commit=True)

    assert result.status == "commit_failed"
    assert result.commit_sha is None
    assert run_git(repo, ["rev-parse", "HEAD"]) == before


def test_pr_assist_manifest_records_itself(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_dir = _write_run_dir(tmp_path, repo=repo)

    run_pr_assist(run_dir=run_dir, overwrite=True)
    payload = json.loads((run_dir / "pr_assist_manifest.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in payload["generated_artifacts"]}

    assert "pr_assist_manifest" in names


def test_run_pr_assist_manifest_invalid_does_not_prepare_branch_or_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    run_dir = _write_run_dir(tmp_path, repo=repo)
    manifest = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        if item["name"] == "patch":
            item["sha256"] = "bad"
    (run_dir / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before_head = run_git(repo, ["rev-parse", "HEAD"])

    result = run_pr_assist(run_dir=run_dir, prepare_branch=True, commit=True, overwrite=True)

    assert result.status == "manifest_invalid"
    assert result.branch_name is None
    assert result.commit_sha is None
    assert run_git(repo, ["rev-parse", "HEAD"]) == before_head
