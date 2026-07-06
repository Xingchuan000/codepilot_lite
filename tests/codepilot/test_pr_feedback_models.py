from __future__ import annotations

from pathlib import Path

from codepilot.pr_feedback.models import PRFeedbackResult, PRFeedbackSafetyError, PRFeedbackStaleHeadError, to_pr_feedback_jsonable


def test_pr_feedback_stale_head_error_inherits_safety_error() -> None:
    assert issubclass(PRFeedbackStaleHeadError, PRFeedbackSafetyError)


def test_pr_feedback_result_defaults() -> None:
    value = PRFeedbackResult(run_id="run-1", run_dir=Path("runs/run-1"), status="planned")

    assert value.dry_run is True
    assert value.execute is False
    assert value.feedback_sources_degraded == []


def test_to_pr_feedback_jsonable_redacts_token_like_strings() -> None:
    payload = to_pr_feedback_jsonable({"value": "ghp_ABC123", "nested": ["Bearer secret-token"]})

    assert payload == {"value": "[REDACTED]", "nested": ["[REDACTED]"]}
