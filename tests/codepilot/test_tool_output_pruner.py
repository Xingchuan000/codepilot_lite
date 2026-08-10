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


def test_pytest_pruner_preserves_assertion_actual_and_expected_values() -> None:
    output = """Test Status: Failed
Command: python -m pytest -q tests/test_pricing.py
Return code: 1
Summary: 1 failed in 0.08s

Failed tests:
- tests/test_pricing.py::test_vip_customer_receives_ten_percent_discount

Relevant output:
E       assert 10.0 == 90.0
E        +  where 10.0 = calculate_total(25.0, 4, vip=True)
tests/test_pricing.py:9: AssertionError
FAILED tests/test_pricing.py::test_vip_customer_receives_ten_percent_discount - assert 10.0 == 90.0
1 failed in 0.08s
"""
    pruned = ToolOutputPruner().prune(
        _route(
            "run_tests",
            ToolResult(
                success=False,
                output=output,
                output_summary="Tests failed: 1 failed in 0.08s.",
                error="Test command failed with returncode 1.",
                metadata={
                    "command": "python -m pytest -q tests/test_pricing.py",
                    "status": "failed",
                    "returncode": 1,
                    "failed_tests": ["tests/test_pricing.py::test_vip_customer_receives_ten_percent_discount"],
                },
            ),
        ),
        token_budget=220,
    )

    assert "Failure diagnostics:" in pruned.content
    assert "assert 10.0 == 90.0" in pruned.content
    assert "where 10.0 = calculate_total" in pruned.content
    assert "tests/test_pricing.py:9: AssertionError" in pruned.content


def test_pytest_pruner_prioritizes_assertion_values_over_noisy_failure_banner() -> None:
    noise = "\n".join(f"noise line {index}" for index in range(200))
    output = f"""{noise}
=================================== FAILURES ===================================
>       assert calculate_total(25.0, 4, vip=True) == 90.0
E       assert 10.0 == 90.0
E        +  where 10.0 = calculate_total(25.0, 4, vip=True)
tests/test_pricing.py:9: AssertionError
FAILED tests/test_pricing.py::test_vip - assert 10.0 == 90.0
1 failed in 0.10s
"""
    pruned = ToolOutputPruner().prune(
        _route(
            "run_tests",
            ToolResult(
                success=False,
                output=output,
                metadata={
                    "command": "pytest -q",
                    "status": "failed",
                    "returncode": 1,
                    "failed_tests": ["tests/test_pricing.py::test_vip"],
                },
            ),
        ),
        token_budget=140,
    )

    assert "assert 10.0 == 90.0" in pruned.content
    assert "where 10.0 = calculate_total" in pruned.content
    assert "noise line 100" not in pruned.content


def test_wait_agent_pruner_preserves_structured_child_evidence() -> None:
    metadata = {
        "agent_id": "sess-child",
        "agent_type": "general",
        "status": "completed",
        "result": "Fixed pricing bug and validated it.",
        "tests": "python -m pytest -q tests/test_pricing.py -> 3 passed",
        "test_status": "passed",
        "diff_checked": True,
        "changed_files": ["src/orderlab/pricing.py"],
        "patch_artifact_id": "art-patch",
        "missing_evidence": [],
    }
    pruned = ToolOutputPruner().prune(
        _route("wait_agent", ToolResult(success=True, output="unused json", metadata=metadata)),
        token_budget=220,
    )

    assert pruned.strategy == "agent_control"
    assert "Test status: passed" in pruned.content
    assert "3 passed" in pruned.content
    assert "Diff checked: True" in pruned.content
    assert "Patch artifact: art-patch" in pruned.content
    assert "Fixed pricing bug and validated it." in pruned.content


def test_wait_agent_pruner_preserves_scope_violation_diagnostics() -> None:
    metadata = {
        "agent_id": "sess-child",
        "agent_type": "general",
        "status": "failed",
        "result": "max_steps_exceeded",
        "error": "max_steps_exceeded; General agent changed files outside write_scope: docs/runtime_marker.txt",
        "changed_files": ["docs/runtime_marker.txt", "src/orderlab/pricing.py"],
        "patch_artifact_id": "art-diagnostic",
        "scope_violation_files": ["docs/runtime_marker.txt"],
        "missing_evidence": [],
    }
    pruned = ToolOutputPruner().prune(
        _route("wait_agent", ToolResult(success=True, output="unused json", metadata=metadata)),
        token_budget=220,
    )

    assert pruned.strategy == "agent_control"
    assert "Status: failed" in pruned.content
    assert "Scope violation files: ['docs/runtime_marker.txt']" in pruned.content
    assert "outside write_scope" in pruned.content
    assert pruned.facts["scope_violation_files"] == ["docs/runtime_marker.txt"]


def test_list_agents_pruner_preserves_multiple_running_children() -> None:
    metadata = {
        "agents": [
            {
                "agent_id": "sess-pricing",
                "agent_type": "general",
                "status": "running",
                "write_scope": ["src/orderlab/pricing.py"],
                "changed_files": [],
            },
            {
                "agent_id": "sess-inventory",
                "agent_type": "general",
                "status": "running",
                "write_scope": ["src/orderlab/inventory.py"],
                "changed_files": [],
            },
        ]
    }
    pruned = ToolOutputPruner().prune(
        _route("list_agents", ToolResult(success=True, output="unused json", metadata=metadata)),
        token_budget=220,
    )

    assert pruned.strategy == "agent_list"
    assert "Agent count: 2" in pruned.content
    assert "Running agents: 2" in pruned.content
    assert "sess-pricing" in pruned.content
    assert "status=running" in pruned.content
    assert "src/orderlab/pricing.py" in pruned.content
    assert "sess-inventory" in pruned.content
    assert "src/orderlab/inventory.py" in pruned.content
    assert pruned.facts["agent_count"] == 2
    assert pruned.facts["running_count"] == 2
    assert len(pruned.facts["agents"]) == 2


def test_list_agents_pruner_keeps_completed_child_evidence_compact() -> None:
    metadata = {
        "agents": [
            {
                "agent_id": "sess-done",
                "agent_type": "general",
                "status": "completed",
                "write_scope": ["src/orderlab/pricing.py"],
                "test_status": "passed",
                "changed_files": ["src/orderlab/pricing.py"],
                "patch_artifact_id": "art-patch",
                "result": "very long semantic child result that list_agents does not need to repeat",
            }
        ]
    }
    pruned = ToolOutputPruner().prune(
        _route("list_agents", ToolResult(success=True, output="unused json", metadata=metadata)),
        token_budget=160,
    )

    assert "status=completed" in pruned.content
    assert "test_status=passed" in pruned.content
    assert "patch_artifact=art-patch" in pruned.content
    assert "very long semantic child result" not in pruned.content
