import json
from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolAction, ToolRouter


def _router(tmp_path: Path, *, approved: bool = False, mode: str = "build") -> ToolRouter:
    return ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="run-test",
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(mode=mode, approved=approved),
    )


def _event_types(tmp_path: Path) -> list[str]:
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["event_type"] for line in lines]


def test_replace_range_without_approval_does_not_write_file(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("a\nb\n", encoding="utf-8")
    router = _router(tmp_path)

    routed = router.route(
        ToolAction(
            tool_name="replace_range",
            arguments={"repo": tmp_path, "path": "demo.py", "start_line": 2, "end_line": 2, "replacement": "x\n"},
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "ask"
    assert routed.metadata["executed"] is False
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "a\nb\n"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_replace_range_with_approval_writes_file(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("a\nb\n", encoding="utf-8")
    router = _router(tmp_path, approved=True)

    routed = router.route(
        ToolAction(
            tool_name="replace_range",
            arguments={"repo": tmp_path, "path": "demo.py", "start_line": 2, "end_line": 2, "replacement": "x\n"},
        )
    )

    assert routed.success is True
    assert routed.metadata["policy_decision"] == "ask"
    assert routed.metadata["approved"] is True
    assert routed.metadata["executed"] is True
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "a\nx\n"
    assert _event_types(tmp_path) == ["policy_decision", "tool_call"]


def test_replace_range_env_path_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(
        ToolAction(
            tool_name="replace_range",
            arguments={"repo": tmp_path, "path": ".env", "start_line": 1, "end_line": 1, "replacement": "x\n"},
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_replace_range_read_only_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="read_only")

    routed = router.route(
        ToolAction(
            tool_name="replace_range",
            arguments={"repo": tmp_path, "path": "demo.py", "start_line": 1, "end_line": 1, "replacement": "x\n"},
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_apply_patch_without_approval_does_not_write_file(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    router = _router(tmp_path)

    routed = router.route(
        ToolAction(
            tool_name="apply_patch",
            arguments={
                "repo": tmp_path,
                "patch": "diff --git a/src/demo.py b/src/demo.py\n--- a/src/demo.py\n+++ b/src/demo.py\n@@ -1 +1 @@\n-old\n+new\n",
            },
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "ask"
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "old\n"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_apply_patch_with_approval_applies_patch(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    router = _router(tmp_path, approved=True)

    routed = router.route(
        ToolAction(
            tool_name="apply_patch",
            arguments={
                "repo": tmp_path,
                "patch": "diff --git a/src/demo.py b/src/demo.py\n--- a/src/demo.py\n+++ b/src/demo.py\n@@ -1 +1 @@\n-old\n+new\n",
            },
        )
    )

    assert routed.success is True
    assert routed.metadata["executed"] is True
    assert "src/demo.py" in routed.result.metadata["touched_paths"]
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "new\n"
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[1])["tool_name"] == "apply_patch"
    assert json.loads(lines[1])["risk"] == "local_write"
    assert json.loads(lines[1])["side_effect"] == "local_write"
    assert json.loads(lines[1])["default_permission"] == "ask"
    assert json.loads(lines[1])["metadata"]["touched_paths"] == ["src/demo.py"]


def test_apply_patch_env_path_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(
        ToolAction(
            tool_name="apply_patch",
            arguments={
                "repo": tmp_path,
                "patch": "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-A=1\n+A=2\n",
            },
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_apply_patch_missing_paths_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(ToolAction(tool_name="apply_patch", arguments={"repo": tmp_path, "patch": "just text\n"}))

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_apply_patch_read_only_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path, mode="read_only")

    routed = router.route(
        ToolAction(
            tool_name="apply_patch",
            arguments={
                "repo": tmp_path,
                "patch": "diff --git a/src/demo.py b/src/demo.py\n--- a/src/demo.py\n+++ b/src/demo.py\n@@ -1 +1 @@\n-old\n+new\n",
            },
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_policy_denies_replace_range_for_github_workflows_even_when_approved(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    router = _router(tmp_path, approved=True)

    routed = router.route(
        ToolAction(
            tool_name="replace_range",
            arguments={
                "repo": tmp_path,
                "path": ".github/workflows/ci.yml",
                "start_line": 1,
                "end_line": 1,
                "replacement": "changed\n",
            },
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert (tmp_path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == "name: ci\n"
    assert _event_types(tmp_path) == ["policy_decision"]


def test_policy_denies_apply_patch_for_runs_directory(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    router = _router(tmp_path, approved=True)

    routed = router.route(
        ToolAction(
            tool_name="apply_patch",
            arguments={
                "repo": tmp_path,
                "patch": "diff --git a/runs/foo.txt b/runs/foo.txt\n--- a/runs/foo.txt\n+++ b/runs/foo.txt\n@@ -0,0 +1 @@\n+demo\n",
            },
        )
    )

    assert routed.success is False
    assert routed.metadata["policy_decision"] == "deny"
    assert _event_types(tmp_path) == ["policy_decision"]
