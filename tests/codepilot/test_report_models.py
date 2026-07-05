from codepilot.report.models import DiffReport, PolicyReport, RunReport, TestReport, ToolStepReport


def test_report_models_support_empty_defaults() -> None:
    report = RunReport(run_id="run-test")

    assert report.run_id == "run-test"
    assert report.steps == 0
    assert report.tests == TestReport()
    assert report.policy == PolicyReport()
    assert report.diff == DiffReport()
    assert report.tool_steps == []


def test_tool_step_report_keeps_preview_structures() -> None:
    step = ToolStepReport(tool_name="read_file", arguments_preview={"path": "src/demo.py"}, metadata={"duration_ms": 1})

    assert step.tool_name == "read_file"
    assert step.arguments_preview["path"] == "src/demo.py"
    assert step.metadata["duration_ms"] == 1
