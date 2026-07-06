from __future__ import annotations

from codepilot.auto_pr.models import AutoPRSafetyError
from codepilot.auto_pr.safety import assert_remote_side_effect_allowed, build_auto_pr_safety_gate


def _pr_assist_manifest() -> dict:
    return {
        "safety_gate": {"status": "pass", "reasons": [], "warnings": []},
        "branch_name": "codepilot/issue-test",
        "commit_sha": "abc123",
        "side_effects": {
            "branch_prepared": True,
            "commit_prepared": True,
            "push_executed": False,
            "pr_created": False,
            "github_api_called": False,
        },
    }


def _source_manifest() -> dict:
    return {
        "status": "success",
        "safety_decision": "allow",
        "safety_summary": {"baseline_dirty": False},
        "patch": {
            "is_empty": False,
            "changed_files": ["src/a.py"],
            "protected_changed_files": [],
            "protected_after_files": [],
        },
    }


def test_build_auto_pr_safety_gate_passes_for_safe_non_empty_patch() -> None:
    assert build_auto_pr_safety_gate(
        pr_assist_manifest=_pr_assist_manifest(),
        source_artifact_manifest=_source_manifest(),
    ).status == "pass"


def test_build_auto_pr_safety_gate_warns_on_pr_assist_warn() -> None:
    manifest = _pr_assist_manifest()
    manifest["safety_gate"]["status"] = "warn"

    assert build_auto_pr_safety_gate(pr_assist_manifest=manifest, source_artifact_manifest=_source_manifest()).status == "fail"


def test_build_auto_pr_safety_gate_fails_when_source_denies() -> None:
    source = _source_manifest()
    source["safety_decision"] = "deny"

    assert build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=source).status == "fail"


def test_build_auto_pr_safety_gate_rejects_empty_patch_by_default() -> None:
    source = _source_manifest()
    source["patch"]["is_empty"] = True

    assert build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=source).status == "fail"


def test_build_auto_pr_safety_gate_allows_empty_patch_when_requested() -> None:
    source = _source_manifest()
    source["patch"]["is_empty"] = True

    assert "empty patch is not allowed" not in build_auto_pr_safety_gate(
        pr_assist_manifest=_pr_assist_manifest(),
        source_artifact_manifest=source,
        allow_empty_pr=True,
    ).reasons


def test_build_auto_pr_safety_gate_rejects_runs_paths() -> None:
    source = _source_manifest()
    source["patch"]["changed_files"] = ["runs/issue/a.md"]

    assert build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=source).status == "fail"


def test_build_auto_pr_safety_gate_rejects_env_file() -> None:
    source = _source_manifest()
    source["patch"]["changed_files"] = [".env"]

    assert build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=source).status == "fail"


def test_build_auto_pr_safety_gate_rejects_workflow_file() -> None:
    source = _source_manifest()
    source["patch"]["changed_files"] = [".github/workflows/x.yml"]

    assert build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=source).status == "fail"


def test_build_auto_pr_safety_gate_rejects_non_codepilot_branch() -> None:
    manifest = _pr_assist_manifest()
    manifest["branch_name"] = "feature/x"

    assert build_auto_pr_safety_gate(pr_assist_manifest=manifest, source_artifact_manifest=_source_manifest()).status == "fail"


def test_build_auto_pr_safety_gate_rejects_missing_commit() -> None:
    manifest = _pr_assist_manifest()
    manifest["commit_sha"] = None

    assert build_auto_pr_safety_gate(pr_assist_manifest=manifest, source_artifact_manifest=_source_manifest()).status == "fail"


def test_assert_remote_side_effect_allowed_requires_allow_push() -> None:
    gate = build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=_source_manifest())

    try:
        assert_remote_side_effect_allowed(safety_gate=gate, execute=True, allow_push=False, allow_create_pr=False)
    except AutoPRSafetyError:
        pass
    else:
        raise AssertionError("expected AutoPRSafetyError")


def test_assert_remote_side_effect_allowed_requires_allow_push_for_pr_create() -> None:
    gate = build_auto_pr_safety_gate(pr_assist_manifest=_pr_assist_manifest(), source_artifact_manifest=_source_manifest())

    try:
        assert_remote_side_effect_allowed(safety_gate=gate, execute=True, allow_push=False, allow_create_pr=True)
    except AutoPRSafetyError:
        pass
    else:
        raise AssertionError("expected AutoPRSafetyError")
