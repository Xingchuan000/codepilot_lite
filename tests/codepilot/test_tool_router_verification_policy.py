import json
import subprocess
from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter


def _router(tmp_path: Path, context: PolicyContext | None = None) -> ToolRouter:
    return ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="run-test",
        policy_checker=PolicyChecker.default(),
        policy_context=context or PolicyContext(mode="build"),
    )


def _write_pytest_repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return tmp_path


def _init_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/demo.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_router_run_tests_safe_command_executes_and_traces(tmp_path: Path) -> None:
    router = _router(_write_pytest_repo(tmp_path))

    routed = router.route({"tool_name": "run_tests", "arguments": {"repo": tmp_path, "command": "python -m pytest -q"}})

    assert routed.success is True
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["event_type"] for event in events] == ["policy_decision", "tool_call"]
    assert events[1]["risk"] == "local_execution"
    assert events[1]["side_effect"] == "local_exec"


def test_router_run_tests_read_only_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(_write_pytest_repo(tmp_path), PolicyContext(mode="read_only"))

    routed = router.route({"tool_name": "run_tests", "arguments": {"repo": tmp_path, "command": "pytest -q"}})

    assert routed.success is False
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_router_run_tests_dangerous_command_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route({"tool_name": "run_tests", "arguments": {"repo": tmp_path, "command": "curl http://example.com"}})

    assert routed.success is False
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_router_git_status_executes_and_traces(tmp_path: Path) -> None:
    router = _router(_init_git_repo(tmp_path))

    routed = router.route({"tool_name": "git_status", "arguments": {"repo": tmp_path}})

    assert routed.success is True
    assert "changed_files" in routed.result.metadata
    assert "clean" in routed.result.metadata
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]


def test_router_git_diff_summary_executes_and_traces(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    (repo / "src" / "demo.py").write_text("new\n", encoding="utf-8")
    router = _router(repo)

    routed = router.route({"tool_name": "git_diff", "arguments": {"repo": repo, "include_content": False}})

    assert routed.success is True
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]


def test_router_git_diff_env_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route({"tool_name": "git_diff", "arguments": {"repo": tmp_path, "path": ".env", "include_content": True}})

    assert routed.success is False
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_router_git_diff_content_without_path_denied_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route({"tool_name": "git_diff", "arguments": {"repo": tmp_path, "include_content": True}})

    assert routed.success is False
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_router_metadata_contains_policy_fields(tmp_path: Path) -> None:
    router = _router(_write_pytest_repo(tmp_path))

    routed = router.route({"tool_name": "run_tests", "arguments": {"repo": tmp_path, "command": "python -m pytest -q"}})

    for key in ("policy_decision", "policy_mode", "approved", "executed"):
        assert key in routed.metadata
