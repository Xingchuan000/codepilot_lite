from codepilot.tools.test_summary import summarize_test_output


def test_summarize_passed_pytest_output_extracts_summary_line() -> None:
    summary = summarize_test_output("header\n3 passed in 0.08s\n", returncode=0)

    assert summary.status == "passed"
    assert summary.summary_line == "3 passed in 0.08s"


def test_summarize_failed_pytest_output_extracts_failed_tests() -> None:
    summary = summarize_test_output(
        "FAILED tests/test_calc.py::test_add - AssertionError\n1 failed, 2 passed in 0.12s\n",
        returncode=1,
    )

    assert summary.status == "failed"
    assert summary.failed_tests == ["tests/test_calc.py::test_add"]


def test_summarize_error_lines_extracts_traceback_and_assertion_error() -> None:
    summary = summarize_test_output(
        "Traceback (most recent call last):\nE   AssertionError: boom\n1 failed in 0.12s\n",
        returncode=1,
    )

    assert summary.error_lines


def test_summarize_timed_out_status_wins() -> None:
    assert summarize_test_output("3 passed in 0.08s\n", returncode=0, timed_out=True).status == "timed_out"


def test_summarize_failed_tests_are_truncated() -> None:
    output = "\n".join(f"FAILED tests/test_calc.py::test_{i} - AssertionError" for i in range(12))
    summary = summarize_test_output(output, returncode=1, max_failed_tests=10)

    assert summary.failed_tests_truncated is True
    assert len(summary.failed_tests) == 10


def test_summarize_relevant_output_is_truncated_by_chars() -> None:
    output = "\n".join(["E   " + ("x" * 100)] * 20)
    summary = summarize_test_output(output, returncode=1, max_chars=50)

    assert summary.relevant_output_truncated is True


def test_summarize_no_tests_ran_summary_line() -> None:
    assert summarize_test_output("no tests ran in 0.01s\n", returncode=5).summary_line == "no tests ran in 0.01s"
