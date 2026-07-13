from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from codepilot.post_pr.models import (
    ApprovalDecision,
    ArtifactSnapshotEntry,
    PostPRAutomationInput,
    PostPRAutomationResult,
    PostPRAutomationState,
    PostPRRoundRef,
    SideEffectEntry,
    SideEffectLedger,
    to_post_pr_jsonable,
    validate_max_rounds,
)


@dataclass(frozen=True)
class _Payload:
    path: Path
    nested: dict[str, str]


def test_post_pr_input_defaults() -> None:
    assert PostPRAutomationInput(run_id="r", run_dir=Path("a"), auto_pr_manifest_path=Path("b")).dry_run is True
    assert PostPRAutomationInput(run_id="r", run_dir=Path("a"), auto_pr_manifest_path=Path("b")).execute is False
    assert PostPRAutomationInput(run_id="r", run_dir=Path("a"), auto_pr_manifest_path=Path("b")).max_rounds == 2


@pytest.mark.parametrize(("value",), [(0,), (4,)])
def test_validate_max_rounds_rejects_out_of_range(value: int) -> None:
    with pytest.raises(ValueError):
        validate_max_rounds(value)


def test_to_post_pr_jsonable_serializes_paths_and_dataclasses() -> None:
    payload = _Payload(path=Path("/tmp/demo"), nested={"name": "value"})
    assert to_post_pr_jsonable(payload) == {"path": "/tmp/demo", "nested": {"name": "value"}}


def test_to_post_pr_jsonable_redacts_token_like_values() -> None:
    assert "[REDACTED]" in to_post_pr_jsonable({"note": "Bearer abc.def"} )["note"]


def test_to_post_pr_jsonable_rejects_token_like_keys() -> None:
    with pytest.raises(ValueError):
        to_post_pr_jsonable({"github_token": "abc"})


def test_result_defaults_terminal_reason_none() -> None:
    result = PostPRAutomationResult(run_id="r", run_dir=Path("r"), post_pr_dir=Path("r/post_pr"), status="planned")
    assert result.terminal_reason == "none"


def test_round_ref_and_side_effect_entry_are_jsonable() -> None:
    round_ref = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=Path("r/post_pr/round-001"))
    effect = SideEffectEntry(round_id="round-001", action="run_agent", status="planned")
    assert to_post_pr_jsonable(round_ref)["round_id"] == "round-001"
    assert to_post_pr_jsonable(effect)["action"] == "run_agent"


def test_typed_state_and_ledger_are_jsonable_without_changing_v1_shape() -> None:
    round_ref = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=Path("r/post_pr/round-001"))
    state = PostPRAutomationState(
        schema_version="codepilot.post_pr.state.v1",
        run_id="r",
        run_dir=Path("r"),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        max_rounds=2,
        rounds=(round_ref,),
        latest_round_id=round_ref.round_id,
    )
    ledger = SideEffectLedger(
        schema_version="codepilot.post_pr.side_effects.v1",
        run_id="r",
        effects=(SideEffectEntry(round_id=round_ref.round_id, action="commit", status="succeeded", commit_sha="abc"),),
    )

    assert to_post_pr_jsonable(state)["rounds"] == [to_post_pr_jsonable(round_ref)]
    assert to_post_pr_jsonable(ledger)["effects"][0]["commit_sha"] == "abc"


def test_approval_decision_round_trips() -> None:
    decision = ApprovalDecision(run_id="r", round_id="round-001", status="pending")
    assert decision.status == "pending"
