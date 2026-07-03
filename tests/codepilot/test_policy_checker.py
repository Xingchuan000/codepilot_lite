from pathlib import Path

import pytest

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.policy.models import PolicyDecision
from codepilot.router.actions import ToolAction


def _decision(tool_name: str, arguments: dict[str, object], context: PolicyContext | None = None) -> PolicyDecision:
    return PolicyChecker.default().check(ToolAction(tool_name=tool_name, arguments=arguments), context=context)


def test_policy_checker_allows_read_only_tools(tmp_path: Path) -> None:
    assert _decision("list_files", {"repo": tmp_path, "path": "."}).allowed is True
    assert _decision("read_file", {"repo": tmp_path, "path": "src/codepilot/tools/base.py"}).allowed is True
    assert _decision("search_code", {"repo": tmp_path, "query": "ToolResult"}).allowed is True
    assert _decision("git_status", {"repo": tmp_path}).allowed is True


@pytest.mark.parametrize(("path",), [(".env",), ("./.env",), ("src/../.env",)])
def test_policy_checker_denies_sensitive_paths(tmp_path: Path, path: str) -> None:
    decision = _decision("read_file", {"repo": tmp_path, "path": path})

    assert decision.denied is True
    assert decision.matched_rule is not None


def test_policy_checker_denies_sensitive_directories(tmp_path: Path) -> None:
    assert _decision("read_file", {"repo": tmp_path, "path": "secrets/token.txt"}).denied is True
    assert _decision("list_files", {"repo": tmp_path, "path": "secrets"}).denied is True
    assert _decision("list_files", {"repo": tmp_path, "path": ".ssh"}).denied is True


def test_policy_checker_denies_paths_outside_repo(tmp_path: Path) -> None:
    assert _decision("read_file", {"repo": tmp_path, "path": "../outside.txt"}).denied is True

    inside_env = tmp_path / ".env"
    inside_env.write_text("SECRET=demo\n", encoding="utf-8")
    assert _decision("read_file", {"repo": tmp_path, "path": str(inside_env)}).denied is True

    outside_path = tmp_path.parent / "outside.txt"
    assert _decision("read_file", {"repo": tmp_path, "path": str(outside_path)}).denied is True


def test_policy_checker_allows_safe_shell_prefixes_in_build_mode(tmp_path: Path) -> None:
    context = PolicyContext(mode="build")

    assert _decision("run_shell", {"repo": tmp_path, "command": "pytest tests/codepilot -q"}, context=context).allowed
    assert _decision("run_shell", {"repo": tmp_path, "command": "python -m pytest tests/codepilot -q"}, context=context).allowed
    assert _decision("run_tests", {"repo": tmp_path, "command": "pytest -q"}, context=context).allowed


def test_policy_checker_denies_dangerous_shell_commands(tmp_path: Path) -> None:
    assert _decision("run_shell", {"repo": tmp_path, "command": "rm -rf ."}).denied is True
    assert _decision("run_shell", {"repo": tmp_path, "command": "  git   push origin main"}).denied is True
    assert _decision("run_tests", {"repo": tmp_path, "command": "rm -rf ."}).denied is True
    assert _decision("run_tests", {"repo": tmp_path, "command": "curl http://example.com"}).denied is True


def test_policy_checker_asks_for_unspecified_shell_commands(tmp_path: Path) -> None:
    assert _decision("run_shell", {"repo": tmp_path, "command": "echo hello"}).asks is True
    assert _decision("run_shell", {"repo": tmp_path, "command": "python scripts/custom.py"}).asks is True
    assert _decision("run_shell", {"repo": tmp_path, "command": 'python -c "print(1)"'}).asks is True
    assert _decision("run_tests", {"repo": tmp_path, "command": "echo hi"}).asks is True


def test_policy_checker_denies_shell_in_read_only_mode(tmp_path: Path) -> None:
    decision = _decision("run_shell", {"repo": tmp_path, "command": "pytest tests/codepilot -q"}, context=PolicyContext(mode="read_only"))

    assert decision.denied is True
    assert decision.matched_rule == "mode.read_only.side_effect.deny"


def test_policy_checker_denies_run_tests_in_read_only_mode(tmp_path: Path) -> None:
    decision = _decision("run_tests", {"repo": tmp_path, "command": "pytest -q"}, context=PolicyContext(mode="read_only"))

    assert decision.denied is True
    assert decision.matched_rule == "mode.read_only.side_effect.deny"


def test_policy_checker_denies_git_diff_sensitive_or_unscoped_content(tmp_path: Path) -> None:
    assert _decision("git_diff", {"repo": tmp_path, "path": ".env", "include_content": True}).denied is True
    decision = _decision("git_diff", {"repo": tmp_path, "include_content": True})
    assert decision.denied is True
    assert decision.matched_rule == "git_diff.content_without_path.deny"


def test_policy_checker_allows_unknown_tools_to_reach_registry() -> None:
    decision = _decision("unknown_tool", {})

    assert decision.allowed is True
    assert decision.matched_rule == "tool.unknown.allow_to_registry"
    assert decision.metadata["known_tool"] is False
