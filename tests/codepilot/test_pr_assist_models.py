from pathlib import Path

from codepilot.pr_assist.models import (
    ManualCommandPlan,
    PRAssistInput,
    PRAssistResult,
    PRAssistSafetyGate,
    to_pr_assist_jsonable,
)


def test_pr_assist_input_defaults() -> None:
    value = PRAssistInput(run_id="run-1", run_dir=Path("runs/run-1"), manifest_path=Path("runs/run-1/artifact_manifest.json"))

    assert value.redact_absolute_paths is True
    assert value.strict_safety is True


def test_manual_command_plan_defaults() -> None:
    plan = ManualCommandPlan(commands=["echo demo"])

    assert plan.push_commands_included is False
    assert plan.pr_create_commands_included is False


def test_pr_assist_result_defaults() -> None:
    result = PRAssistResult(
        run_id="run-1",
        run_dir=Path("runs/run-1"),
        status="generated",
        safety_gate=PRAssistSafetyGate(status="pass"),
    )

    assert result.branch_name is None
    assert result.commit_sha is None


def test_to_pr_assist_jsonable_converts_path() -> None:
    payload = to_pr_assist_jsonable({"path": Path("demo.txt")})

    assert payload == {"path": "demo.txt"}


def test_pr_assist_safety_gate_literal_values() -> None:
    assert PRAssistSafetyGate(status="pass").status == "pass"
    assert PRAssistSafetyGate(status="warn").status == "warn"
    assert PRAssistSafetyGate(status="fail").status == "fail"
