from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router.actions import ToolAction


def _check(tool_name: str, arguments: dict[str, object], context: PolicyContext | None = None):
    return PolicyChecker.default().check(ToolAction(tool_name=tool_name, arguments=arguments), context=context)


def test_policy_run_tests_safe_pytest_allowed_in_build_mode(tmp_path: Path) -> None:
    assert _check("run_tests", {"repo": tmp_path, "command": "pytest -q"}, PolicyContext(mode="build")).allowed


def test_policy_run_tests_python_m_pytest_allowed_in_build_mode(tmp_path: Path) -> None:
    assert _check("run_tests", {"repo": tmp_path, "command": "python -m pytest tests -q"}, PolicyContext(mode="build")).allowed


def test_policy_run_tests_npm_test_allowed_in_build_mode(tmp_path: Path) -> None:
    assert _check("run_tests", {"repo": tmp_path, "command": "npm test"}, PolicyContext(mode="build")).allowed


def test_policy_run_tests_denied_in_read_only_mode(tmp_path: Path) -> None:
    decision = _check("run_tests", {"repo": tmp_path, "command": "pytest -q"}, PolicyContext(mode="read_only"))

    assert decision.denied is True
    assert decision.matched_rule == "mode.read_only.side_effect.deny"


def test_policy_run_tests_curl_denied(tmp_path: Path) -> None:
    assert _check("run_tests", {"repo": tmp_path, "command": "curl http://example.com"}).denied is True


def test_policy_run_tests_git_push_denied(tmp_path: Path) -> None:
    assert _check("run_tests", {"repo": tmp_path, "command": "pytest && git push"}).denied is True


def test_policy_git_status_allowed_in_read_only_mode(tmp_path: Path) -> None:
    assert _check("git_status", {"repo": tmp_path}, PolicyContext(mode="read_only")).allowed


def test_policy_git_diff_summary_allowed_in_read_only_mode(tmp_path: Path) -> None:
    assert _check("git_diff", {"repo": tmp_path, "include_content": False}, PolicyContext(mode="read_only")).allowed


def test_policy_git_diff_env_path_denied(tmp_path: Path) -> None:
    assert _check("git_diff", {"repo": tmp_path, "path": ".env", "include_content": True}).denied is True


def test_policy_git_diff_env_path_normalized_denied(tmp_path: Path) -> None:
    assert _check("git_diff", {"repo": tmp_path, "path": "src/../.env", "include_content": True}).denied is True


def test_policy_git_diff_content_without_path_denied(tmp_path: Path) -> None:
    decision = _check("git_diff", {"repo": tmp_path, "include_content": True})

    assert decision.denied is True
    assert decision.matched_rule == "git_diff.content_without_path.deny"


def test_policy_git_diff_safe_path_allowed(tmp_path: Path) -> None:
    assert _check("git_diff", {"repo": tmp_path, "path": "src/demo.py", "include_content": True}).allowed
