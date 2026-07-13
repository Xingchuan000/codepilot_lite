from pathlib import Path

from codepilot.agent.outcome import build_run_outcome
from codepilot.agent.state import create_initial_state


def test_build_run_outcome_contains_status_and_evidence_payload(tmp_path: Path) -> None:
    state = create_initial_state("fix add", tmp_path, max_steps=2)
    state.completion_kind = "task_success"
    state.assistant_stop_reason = "structured_finish"
    state.delivery_kind = "code_change"
    state.requires_evidence = True
    state.evidence_reasons = ["write_executed", "written_files"]
    state.write_attempted = True
    state.write_executed = True
    state.written_files = ["src/calc.py", "tests/test_calc.py"]
    state.observed_changed_files = ["src/calc.py"]
    state.claimed_changed_files = ["src/calc.py"]
    state.tests_required = True
    state.diff_required = True
    state.diff_checked = True
    state.missing_evidence = []
    state.changed_files = ["src/calc.py", "tests/test_calc.py"]
    state.last_test_status = "passed"

    outcome = build_run_outcome(state, status="success")

    assert outcome.to_payload() == {
        "status": "success",
        "completion_kind": "task_success",
        "assistant_stop_reason": "structured_finish",
        "delivery_kind": "code_change",
        "changed_files": ["src/calc.py", "tests/test_calc.py"],
        "test_status": "passed",
        "requires_evidence": True,
        "evidence_reasons": ["write_executed", "written_files"],
        "write_attempted": True,
        "write_executed": True,
        "written_files": ["src/calc.py", "tests/test_calc.py"],
        "observed_changed_files": ["src/calc.py"],
        "claimed_changed_files": ["src/calc.py"],
        "tests_required": True,
        "diff_required": True,
        "diff_checked": True,
        "missing_evidence": [],
    }
