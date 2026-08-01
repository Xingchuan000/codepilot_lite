from codepilot.agent.tool_output_pruner import ToolOutputPruner
from codepilot.router.actions import ToolRouteResult
from codepilot.session.context_budget import estimate_tokens
from codepilot.tools.base import ToolResult


def _route(tool_name: str, result: ToolResult) -> ToolRouteResult:
    return ToolRouteResult(action_id="action", tool_name=tool_name, success=result.success, result=result)


def test_prunes_large_pytest_output_to_failure_and_final_summary() -> None:
    output = "\n".join([*(f"tests/test_many.py::test_{index} PASSED" for index in range(3000)), "E AssertionError: expected completed", "1 failed, 2999 passed in 20.0s"])
    pruned = ToolOutputPruner().prune(
        _route("run_tests", ToolResult(success=False, output=output, metadata={"command": "python -m pytest -q", "status": "failed", "returncode": 1, "failed_tests": ["tests/test_many.py::test_bad"]})),
        token_budget=300,
    )

    assert pruned.strategy == "pytest"
    assert pruned.truncated is True
    assert "test_bad" in pruned.content
    assert "AssertionError" in pruned.content
    assert "1 failed, 2999 passed" in pruned.content
    assert "test_1000 PASSED" not in pruned.content


def test_pruner_redacts_mcp_secret_and_keeps_trust_metadata() -> None:
    pruned = ToolOutputPruner().prune(
        _route("external_search", ToolResult(success=True, output="token=super-secret-value", metadata={"mcp": True, "server_name": "demo", "mcp_tool_name": "search", "trust_level": "untrusted"})),
        token_budget=256,
    )

    assert "super-secret-value" not in pruned.content
    assert "[REDACTED_SECRET]" in pruned.content
    assert "untrusted" in pruned.content


def test_error_only_result_reports_real_truncation() -> None:
    error = "failure " * 1000
    route = _route("unknown", ToolResult(success=False, output="", error=error))
    pruned = ToolOutputPruner().prune(route, token_budget=256)

    assert pruned.original_chars >= len(error)
    assert pruned.truncated is True
    assert pruned.length_truncated is True
    assert estimate_tokens(pruned.content) <= 256
    assert route.result.error == error


def test_small_observation_reports_semantic_transformation() -> None:
    pruned = ToolOutputPruner().prune(
        _route("read_file", ToolResult(success=True, output="one line", metadata={"path": "a.py"})),
        token_budget=256,
    )

    assert pruned.transformed is True
    assert pruned.length_truncated is False
    assert pruned.truncated is True


def test_long_chinese_output_obeys_estimated_token_budget() -> None:
    pruned = ToolOutputPruner().prune(
        _route("unknown", ToolResult(success=True, output="中文输出" * 5000)),
        token_budget=128,
    )

    assert estimate_tokens(pruned.content) <= 128
    assert "truncated" in pruned.content


def test_search_result_redacts_plain_secret_assignment() -> None:
    pruned = ToolOutputPruner().prune(
        _route("search_code", ToolResult(success=True, output="config.py:1: API_KEY=super-secret-value-1234567890")),
        token_budget=256,
    )

    assert "super-secret-value-1234567890" not in pruned.content
    assert "API_KEY=[REDACTED_SECRET]" in pruned.content


def test_code_redaction_preserves_dynamic_expressions() -> None:
    pruned = ToolOutputPruner().prune(
        _route("read_file", ToolResult(success=True, output='API_KEY = os.getenv("API_KEY")\npassword = request.form["password"]')),
        token_budget=256,
    )

    assert 'API_KEY = os.getenv("API_KEY")' in pruned.content
    assert 'password = request.form["password"]' in pruned.content


def test_code_second_budget_trim_keeps_consistent_line_ranges() -> None:
    output = "\n".join(f"{index}: code line {index}" for index in range(1, 121))
    pruned = ToolOutputPruner().prune(
        _route("read_file", ToolResult(success=True, output=output, metadata={"path": "x.py", "start_line": 1, "end_line": 120, "actual_start_line": 1, "total_lines": 120})),
        token_budget=80,
    )

    assert estimate_tokens(pruned.content) <= 80
    assert "Visible lines: none, 120-120" in pruned.content
    assert "Omitted lines: 1-119" in pruned.content
    assert "To continue: read_file(path=\"x.py\", start_line=1, end_line=119)" in pruned.content
    assert "... omitted middle lines ..." in pruned.content
