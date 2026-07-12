from codepilot.report.markdown import render_markdown_report
from codepilot.report.models import DiffReport, PolicyReport, PolicyViolationReport, RunReport, TestReport, ToolStepReport


def test_render_markdown_report_has_all_sections() -> None:
    report = RunReport(
        run_id="run-test",
        task="fix bug",
        repo="/repo",
        model="demo",
        policy_mode="build",
        status="success",
        success=True,
        final_summary="done",
        steps=4,
        changed_files=["src/calc.py"],
        trace_path="/repo/run/trace.jsonl",
        tool_steps=[ToolStepReport(tool_name="read_file", step=1, executed=True, success=True, summary="ok")],
        tests=TestReport(status="passed", command="pytest", original_command="pytest", executed_command="python -m pytest", failed_tests=[], summary="ok", returncode=0, timed_out=False),
        policy=PolicyReport(total=1, allowed=1),
        diff=DiffReport(checked=True, paths=["src/calc.py"], summary="diff", preview="diff --git a b\n+line", truncated=False),
    )

    markdown = render_markdown_report(report)

    assert "# CodePilot Lite Run Report" in markdown
    for title in (
        "## 1. Run Summary",
        "## 2. Task",
        "## 3. Final Result",
        "## 4. Evidence Gate",
        "## 5. Tool Timeline",
        "## 6. Files Changed",
        "## 7. Test Result",
        "## 8. Diff Summary",
        "## 9. Policy Summary",
        "## 10. Failure / Warning Notes",
    ):
        assert title in markdown
    assert "Delivery Kind" in markdown
    assert "Evidence Required" in markdown


def test_render_markdown_report_escapes_table_cells() -> None:
    report = RunReport(
        run_id="run-test",
        tool_steps=[ToolStepReport(tool_name="read_file", step=1, executed=True, success=True, summary="a | b\nc")],
    )

    markdown = render_markdown_report(report)

    assert "a \\| b<br>c" in markdown


def test_render_markdown_report_renders_diff_fence() -> None:
    report = RunReport(run_id="run-test", diff=DiffReport(checked=True, preview="diff --git a b\n+line", summary="ok"))

    markdown = render_markdown_report(report)

    assert "```diff" in markdown


def test_render_markdown_report_truncates_long_diff_preview() -> None:
    report = RunReport(run_id="run-test", diff=DiffReport(checked=True, preview="x" * 5005))

    markdown = render_markdown_report(report)

    assert "... truncated" in markdown


def test_render_markdown_report_redacts_sensitive_text() -> None:
    report = RunReport(run_id="run-test", warnings=["api_key=secret-value"], changed_files=[], tests=TestReport())

    markdown = render_markdown_report(report)

    assert "secret-value" not in markdown


def test_render_markdown_report_shows_empty_values() -> None:
    markdown = render_markdown_report(RunReport(run_id="run-test"))

    assert "None." in markdown
    assert "none" in markdown


def test_render_markdown_report_marks_validation_not_required() -> None:
    report = RunReport(
        run_id="run-test",
        status="message_complete",
        tests_required=False,
        diff_required=False,
        tests=TestReport(),
        diff=DiffReport(),
    )

    markdown = render_markdown_report(report)

    assert "- Status: not required" in markdown
    assert "Diff was not required." in markdown
