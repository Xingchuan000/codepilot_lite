from pathlib import Path

from codepilot.report.extractor import build_run_report


def _event(event_type: str, *, step: int, run_id: str = "run-test", **kwargs: object) -> dict[str, object]:
    return {
        "schema_version": "trace.v1",
        "run_id": run_id,
        "step": step,
        "event_type": event_type,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "tool_name": kwargs.pop("tool_name", None),
        "risk": kwargs.pop("risk", None),
        "side_effect": kwargs.pop("side_effect", None),
        "default_permission": kwargs.pop("default_permission", None),
        "policy_decision": kwargs.pop("policy_decision", None),
        "policy_reason": kwargs.pop("policy_reason", None),
        "policy_rule": kwargs.pop("policy_rule", None),
        "policy_mode": kwargs.pop("policy_mode", None),
        "input": kwargs.pop("input", {}),
        "success": kwargs.pop("success", None),
        "output_summary": kwargs.pop("output_summary", None),
        "output_preview": kwargs.pop("output_preview", None),
        "error": kwargs.pop("error", None),
        "metadata": kwargs.pop("metadata", {}),
        **kwargs,
    }


def test_build_run_report_success_trace(tmp_path: Path) -> None:
    repo = tmp_path
    trace_path = repo / "runs" / "run-test" / "trace.jsonl"
    events = [
        _event("run_start", step=1, metadata={"task": "fix add", "repo": str(repo), "max_steps": 8, "policy_mode": "build"}),
        _event("llm_call", step=2, metadata={"model": "demo-model"}),
        _event("agent_action", step=3, tool_name="read_file", success=True),
        _event("policy_decision", step=4, tool_name="read_file", policy_decision="allow", metadata={"approved": True, "executed": True}),
        _event("tool_call", step=5, tool_name="read_file", success=True, output_summary="Read file.", metadata={"path": "src/calc.py"}),
        _event("agent_action", step=6, tool_name="replace_range", success=True),
        _event("policy_decision", step=7, tool_name="replace_range", policy_decision="ask", metadata={"approved": True, "executed": True}),
        _event(
            "tool_call",
            step=8,
            tool_name="replace_range",
            success=True,
            output_summary="Replaced lines.",
            metadata={"path": str(repo / "src/calc.py"), "changed": True},
        ),
        _event("policy_decision", step=9, tool_name="run_tests", policy_decision="ask", metadata={"approved": True, "executed": True}),
        _event(
            "tool_call",
            step=10,
            tool_name="run_tests",
            success=True,
            output_summary="Tests passed.",
            metadata={
                "status": "passed",
                "command": "pytest",
                "original_command": "pytest",
                "executed_command": "python -m pytest",
                "failed_tests": [],
                "returncode": 0,
                "timed_out": False,
            },
        ),
        _event("policy_decision", step=11, tool_name="git_status", policy_decision="allow", metadata={"approved": True, "executed": True}),
        _event("tool_call", step=12, tool_name="git_status", success=True, output_summary="Repository has 1 changed file(s).", metadata={"changed_files": [str(repo / "src/calc.py")]}),
        _event("policy_decision", step=13, tool_name="git_diff", policy_decision="allow", metadata={"approved": True, "executed": True}),
        _event(
            "tool_call",
            step=14,
            tool_name="git_diff",
            success=True,
            output_summary="Returned git diff summary.",
            output_preview="diff --git a/src/calc.py b/src/calc.py\n+return a + b\n",
            metadata={"path": str(repo / "src/calc.py")},
        ),
        _event("agent_finish", step=15, success=True, output_summary="Fixed add().", metadata={"status": "success", "changed_files": [str(repo / "src/calc.py")] , "tests": "passed"}),
        _event("run_end", step=16, success=True, output_summary="final summary"),
    ]

    report = build_run_report(events, trace_path=trace_path)

    assert report.run_id == "run-test"
    assert report.task == "fix add"
    assert report.repo == str(repo)
    assert report.model == "demo-model"
    assert report.status == "success"
    assert report.success is True
    assert report.tests.status == "passed"
    assert report.tests.command == "pytest"
    assert report.changed_files == ["src/calc.py"]
    assert report.diff.checked is True
    assert report.diff.paths == ["src/calc.py"]
    assert report.diff.preview and "return a + b" in report.diff.preview
    assert report.policy.allowed == 3
    assert report.policy.asked == 2
    assert report.policy.approved == 5
    assert report.policy.total == 5
    assert report.tool_steps[0].tool_name == "read_file"
    assert report.tool_steps[0].executed is True


def test_build_run_report_handles_policy_deny() -> None:
    events = [
        _event("run_start", step=1, metadata={"task": "fix", "repo": "/repo", "max_steps": 3}),
        _event("policy_decision", step=2, tool_name="replace_range", policy_decision="deny", metadata={"approved": False, "executed": False, "risk": "local_write"}),
        _event("run_end", step=3, success=False, output_summary="max_steps_exceeded"),
    ]

    report = build_run_report(events)

    assert report.policy.denied == 1
    assert report.tool_steps[0].tool_name == "replace_range"
    assert report.tool_steps[0].executed is False
    assert report.policy.violations[0].decision == "deny"
    assert "Missing agent_finish event." in report.warnings


def test_build_run_report_handles_ask_approved() -> None:
    events = [
        _event("policy_decision", step=1, tool_name="run_tests", policy_decision="ask", metadata={"approved": True, "executed": True}),
        _event("tool_call", step=2, tool_name="run_tests", success=True, output_summary="passed", metadata={"status": "passed", "returncode": 0, "timed_out": False}),
    ]

    report = build_run_report(events)

    assert report.tool_steps[0].policy_decision == "ask"
    assert report.tool_steps[0].approved is True
    assert report.tool_steps[0].executed is True


def test_build_run_report_handles_ask_not_approved() -> None:
    events = [_event("policy_decision", step=1, tool_name="replace_range", policy_decision="ask", metadata={"approved": False, "executed": False})]

    report = build_run_report(events)

    assert report.tool_steps[0].executed is False
    assert report.policy.violations[0].decision == "ask"


def test_build_run_report_uses_last_run_tests() -> None:
    events = [
        _event("tool_call", step=1, tool_name="run_tests", success=False, output_summary="failed", metadata={"status": "failed", "command": "pytest", "returncode": 1, "timed_out": False}),
        _event("tool_call", step=2, tool_name="run_tests", success=True, output_summary="passed", metadata={"status": "passed", "command": "pytest -q", "returncode": 0, "timed_out": False}),
    ]

    report = build_run_report(events)

    assert report.tests.status == "passed"
    assert report.tests.command == "pytest -q"


def test_build_run_report_handles_max_steps_exceeded() -> None:
    events = [_event("run_end", step=1, success=False, output_summary="max_steps_exceeded")]

    report = build_run_report(events, trace_path=Path("/tmp/runs/run-abc/trace.jsonl"))

    assert report.run_id == "run-abc"
    assert report.status == "max_steps_exceeded"
    assert "Run ended because max_steps was exceeded." in report.warnings


def test_build_run_report_missing_run_start_uses_trace_path_parent(tmp_path: Path) -> None:
    trace_path = tmp_path / "run-demo" / "trace.jsonl"
    report = build_run_report([_event("run_end", step=1, success=True, output_summary="done")], trace_path=trace_path)

    assert report.run_id == "run-demo"
    assert "Missing run_start event." in report.warnings


def test_build_run_report_truncates_git_diff_preview() -> None:
    events = [_event("tool_call", step=1, tool_name="git_diff", success=True, output_preview="x" * 5005, metadata={"path": "src/calc.py"})]

    report = build_run_report(events)

    assert report.diff.preview and report.diff.preview.endswith("... truncated")
    assert report.diff.truncated is True


def test_build_run_report_redacts_sensitive_metadata() -> None:
    events = [_event("tool_call", step=1, tool_name="read_file", success=True, metadata={"api_key": "abc", "nested": {"token": "x"}, "plain": "ok"})]

    report = build_run_report(events)

    assert report.tool_steps[0].metadata["api_key"] == "[REDACTED]"
    assert report.tool_steps[0].metadata["nested"]["token"] == "[REDACTED]"
    assert report.tool_steps[0].metadata["plain"] == "ok"
