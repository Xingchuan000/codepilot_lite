from pathlib import Path

import pytest
from pydantic import ValidationError

from codepilot.policy import PolicyContext, PolicyDecision


def test_policy_decision_helpers() -> None:
    assert PolicyDecision(decision="allow", reason="ok").allowed is True
    assert PolicyDecision(decision="deny", reason="blocked").denied is True
    assert PolicyDecision(decision="ask", reason="need approval").asks is True


def test_policy_context_defaults_and_path_support() -> None:
    context = PolicyContext(repo=Path("."))

    assert context.mode == "build"
    assert context.approved is False
    assert context.interactive is False
    assert context.repo == Path(".")


def test_policy_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyContext.model_validate({"unexpected": True})


def test_policy_decision_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(decision="maybe", reason="bad")
