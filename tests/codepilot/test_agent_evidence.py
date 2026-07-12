from pathlib import Path

from codepilot.agent.evidence import classify_task_intent, evaluate_evidence, shell_command_may_write
from codepilot.agent.state import create_initial_state, refresh_evidence_state


def test_classify_task_intent_hello_is_general() -> None:
    assert classify_task_intent("hello") == "general"


def test_classify_task_intent_project_explanation_is_general() -> None:
    assert classify_task_intent("请解释项目结构") == "general"


def test_classify_task_intent_read_only_phrase_wins() -> None:
    assert classify_task_intent("不要改代码，只分析失败原因") == "read_only"


def test_classify_task_intent_english_read_only_wins() -> None:
    assert classify_task_intent("do not modify, explain the bug") == "read_only"


def test_classify_task_intent_fix_is_code_delivery() -> None:
    assert classify_task_intent("fix the bug") == "code_delivery"


def test_classify_task_intent_chinese_modify_is_code_delivery() -> None:
    assert classify_task_intent("修复 add bug") == "code_delivery"


def test_shell_command_may_write_detects_redirection() -> None:
    assert shell_command_may_write("echo x > a.txt")


def test_shell_command_may_write_detects_sed_in_place() -> None:
    assert shell_command_may_write("sed -i 's/a/b/' file.txt")


def test_shell_command_may_write_ignores_git_status_and_pytest() -> None:
    assert not shell_command_may_write("git status")
    assert not shell_command_may_write("pytest -q")
    assert not shell_command_may_write("python -m pytest tests/")


def test_shell_command_may_write_detects_file_ops() -> None:
    assert shell_command_may_write("touch a.txt")
    assert shell_command_may_write("mkdir tmp")
    assert shell_command_may_write("git add .")
    assert shell_command_may_write("pip install -r requirements.txt")


def test_write_attempt_without_real_change_reports_missing_execution_and_files() -> None:
    decision = evaluate_evidence(
        task_requires_code_delivery=False,
        write_attempted=True,
        write_executed=False,
        written_files=[],
        observed_changed_files=[],
        claimed_changed_files=[],
        last_test_status=None,
        diff_checked=False,
    )

    assert decision.requires_evidence is True
    assert "missing_write_execution" in decision.missing
    assert "missing_changed_files" in decision.missing
    assert decision.success_allowed is False


def test_written_files_without_tests_requires_passed_tests(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=1)
    state.task_requires_code_delivery = True
    state.written_files = ["src/calc.py"]
    state.write_executed = True
    decision = refresh_evidence_state(state)

    assert decision.tests_required is True
    assert "missing_passed_tests" in decision.missing


def test_passed_tests_without_diff_requires_diff_check(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=1)
    state.task_requires_code_delivery = True
    state.written_files = ["src/calc.py"]
    state.write_executed = True
    state.last_test_status = "passed"
    decision = refresh_evidence_state(state)

    assert decision.diff_required is True
    assert "missing_diff_check" in decision.missing


def test_complete_evidence_allows_success(tmp_path: Path) -> None:
    state = create_initial_state("demo", tmp_path, max_steps=1)
    state.task_requires_code_delivery = True
    state.written_files = ["src/calc.py"]
    state.write_executed = True
    state.last_test_status = "passed"
    state.diff_checked = True
    decision = refresh_evidence_state(state)

    assert decision.success_allowed is True
    assert decision.missing == ()


def test_observed_changed_files_alone_do_not_prove_modification(tmp_path: Path) -> None:
    decision = evaluate_evidence(
        task_requires_code_delivery=True,
        write_attempted=False,
        write_executed=False,
        written_files=[],
        observed_changed_files=["src/calc.py"],
        claimed_changed_files=[],
        last_test_status=None,
        diff_checked=False,
    )

    assert "missing_changed_files" in decision.missing


def test_claimed_changed_files_alone_do_not_trigger_tests_or_diff(tmp_path: Path) -> None:
    decision = evaluate_evidence(
        task_requires_code_delivery=True,
        write_attempted=False,
        write_executed=False,
        written_files=[],
        observed_changed_files=[],
        claimed_changed_files=["src/calc.py"],
        last_test_status=None,
        diff_checked=False,
    )

    assert decision.requires_evidence is True
    assert decision.tests_required is False
    assert decision.diff_required is False
    assert "missing_changed_files" in decision.missing


def test_code_change_without_real_write_reports_missing_write_execution(tmp_path: Path) -> None:
    decision = evaluate_evidence(
        task_requires_code_delivery=True,
        write_attempted=False,
        write_executed=False,
        written_files=[],
        observed_changed_files=[],
        claimed_changed_files=[],
        last_test_status=None,
        diff_checked=False,
    )

    assert "missing_write_execution" in decision.missing
    assert "missing_changed_files" in decision.missing


def test_general_task_without_writes_does_not_require_evidence(tmp_path: Path) -> None:
    state = create_initial_state("hello", tmp_path, max_steps=1)
    decision = refresh_evidence_state(state)

    assert decision.requires_evidence is False
    assert decision.success_allowed is True
