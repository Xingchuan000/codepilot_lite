from __future__ import annotations

from codepilot.post_pr.feedback_delta import (
    build_feedback_delta,
    classify_check_terminal_reason,
    extract_feedback_fingerprints,
    fallback_feedback_fingerprint,
    should_stop_for_repeated_feedback,
)


def test_extract_feedback_fingerprints_filters_noise() -> None:
    manifest = {
        "schema_version": "codepilot.ci_feedback_manifest.v1",
        "safe_summary": {
            "feedback_items": [
                {"severity": "warning", "kind": "ci_failure", "fingerprint": "x"},
                {"severity": "blocking", "kind": "pending_check", "fingerprint": "y"},
                {"severity": "blocking", "kind": "ci_failure", "fingerprint": "z", "stale": True},
                {"severity": "blocking", "kind": "ci_failure", "fingerprint": "resolved", "status": "resolved"},
                {"severity": "error", "kind": "ci_failure", "summary": "line 2026-07-06T00:00:00Z run id 1234"},
            ]
        },
    }
    assert extract_feedback_fingerprints(manifest) == [fallback_feedback_fingerprint(manifest["safe_summary"]["feedback_items"][4])]


def test_extract_feedback_fingerprints_ignores_neutral_and_notes() -> None:
    manifest = {
        "schema_version": "codepilot.ci_feedback_manifest.v1",
        "safe_summary": {
            "feedback_items": [
                {"severity": "blocking", "kind": "review_comment", "conclusion": "neutral", "fingerprint": "neutral"},
                {"severity": "blocking", "kind": "review_comment", "status": "note", "fingerprint": "note"},
                {"severity": "blocking", "kind": "review_comment", "status": "resolved", "fingerprint": "resolved"},
                {"severity": "error", "kind": "ci_failure", "conclusion": "failure", "fingerprint": "actionable"},
            ]
        },
    }
    assert extract_feedback_fingerprints(manifest) == ["actionable"]


def test_build_feedback_delta_marks_repeated_and_progressed() -> None:
    delta = build_feedback_delta(previous_fingerprints=["a"], current_fingerprints=["a"])
    assert delta.is_repeated_failure is True
    assert should_stop_for_repeated_feedback(delta, stop_on_repeated_feedback=True) is True


def test_classify_check_terminal_reason_handles_pending_and_timeout() -> None:
    assert classify_check_terminal_reason({"schema_version": "codepilot.ci_feedback_manifest.v1", "safe_summary": {"checks": [{"conclusion": "pending"}]}}) == "pending_checks"
    assert classify_check_terminal_reason({"schema_version": "codepilot.ci_feedback_manifest.v1", "safe_summary": {"checks": [{"conclusion": "timed_out"}]}}) == "ci_timeout"


def test_fallback_fingerprint_ignores_workflow_run_id_and_timestamp() -> None:
    item = {
        "kind": "ci_failure",
        "source": "github",
        "check_name": "build",
        "file_path": "src/app.py",
        "summary": "failed at 2026-07-06T00:00:00Z",
        "raw_excerpt": "line 1\nline 2",
        "observed_at": "2026-07-06T01:00:00Z",
        "workflow_run_id": 123,
        "job_id": 456,
    }
    assert fallback_feedback_fingerprint(item) == fallback_feedback_fingerprint(
        {
            **item,
            "raw_excerpt": "different excerpt",
            "observed_at": "2026-07-06T02:00:00Z",
            "workflow_run_id": 789,
            "job_id": 987,
        }
    )
