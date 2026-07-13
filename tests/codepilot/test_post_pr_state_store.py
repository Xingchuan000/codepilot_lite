from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.post_pr.models import PostPRAutomationState, PostPRRoundRef, SideEffectEntry, SideEffectLedger
from codepilot.post_pr.state_store import (
    acquire_state_lock,
    append_side_effect,
    atomic_write_json,
    clear_post_pr_dir,
    initial_post_pr_state,
    initial_side_effects,
    latest_successful_commit_for_round,
    load_post_pr_state,
    load_side_effects,
    release_state_lock,
    resolve_post_pr_dir,
    resolve_side_effects_path,
    resolve_state_path,
    upsert_round,
    write_post_pr_state,
)


def test_resolve_post_pr_dir_appends_child() -> None:
    run_dir = Path("/tmp/run")
    assert resolve_post_pr_dir(run_dir) == run_dir.expanduser().resolve() / "post_pr"


def test_resolve_post_pr_dir_handles_relative_path() -> None:
    assert resolve_post_pr_dir("relative-run").name == "post_pr"


def test_state_round_trip_and_lock(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state_path = resolve_state_path(run_dir)
    state = initial_post_pr_state(run_id="r", run_dir=run_dir, max_rounds=2)
    write_post_pr_state(state, state_path)
    loaded = load_post_pr_state(state_path)
    assert loaded is not None
    assert isinstance(loaded, PostPRAutomationState)
    assert loaded.schema_version == "codepilot.post_pr.state.v1"
    round_ref = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=run_dir / "post_pr/round-001")
    updated = upsert_round(loaded, round_ref)
    write_post_pr_state(updated, state_path)
    restored = load_post_pr_state(state_path)
    assert restored is not None
    assert restored.latest_round_id == "round-001"
    assert restored.rounds == (round_ref,)
    lock = acquire_state_lock(resolve_post_pr_dir(run_dir))
    with pytest.raises(RuntimeError):
        acquire_state_lock(resolve_post_pr_dir(run_dir))
    release_state_lock(lock)


def test_clear_post_pr_dir_keeps_root_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    post_pr_dir = resolve_post_pr_dir(run_dir)
    post_pr_dir.mkdir(parents=True)
    (post_pr_dir / "state.json").write_text("{}", encoding="utf-8")
    (run_dir / "auto_pr_manifest.json").write_text("{}", encoding="utf-8")
    clear_post_pr_dir(post_pr_dir)
    assert not (post_pr_dir / "state.json").exists()
    assert (run_dir / "auto_pr_manifest.json").exists()


def test_side_effect_ledger_tracks_commits(tmp_path: Path) -> None:
    ledger_path = resolve_side_effects_path(tmp_path / "run")
    ledger = load_side_effects(ledger_path, run_id="r")
    ledger = append_side_effect(ledger, SideEffectEntry(round_id="round-001", action="commit", status="succeeded", commit_sha="abc"))
    assert latest_successful_commit_for_round(ledger, round_id="round-001") == "abc"
    atomic_write_json(ledger, ledger_path)
    restored = load_side_effects(ledger_path, run_id="r")
    assert isinstance(restored, SideEffectLedger)
    assert restored == ledger


def test_upsert_round_replaces_same_round_without_duplication(tmp_path: Path) -> None:
    state = initial_post_pr_state(run_id="r", run_dir=tmp_path, max_rounds=2)
    original = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=tmp_path / "round-001", status="feedback_found")
    updated = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=tmp_path / "round-001", status="patch_ready")

    state = upsert_round(upsert_round(state, original), updated)

    assert state.rounds == (updated,)
    assert state.latest_round_id == "round-001"


@pytest.mark.parametrize(
    ("filename", "payload", "loader"),
    [
        (
            "state.json",
            {"schema_version": "codepilot.post_pr.state.v0"},
            lambda path: load_post_pr_state(path),
        ),
        (
            "side_effects.json",
            {"schema_version": "codepilot.post_pr.side_effects.v0", "run_id": "r", "effects": []},
            lambda path: load_side_effects(path, run_id="r"),
        ),
    ],
)
def test_state_store_rejects_schema_version_mismatch(tmp_path: Path, filename: str, payload: dict, loader) -> None:
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version mismatch"):
        loader(path)


def test_state_store_rejects_invalid_v1_round_structure(tmp_path: Path) -> None:
    state = initial_post_pr_state(run_id="r", run_dir=tmp_path, max_rounds=2)
    payload = json.loads(json.dumps(state, default=lambda value: value.__dict__ if hasattr(value, "__dict__") else str(value)))
    payload["rounds"] = [{"round_id": "round-001"}]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="round_index"):
        load_post_pr_state(path)


def test_side_effect_store_rejects_invalid_v1_effect_structure(tmp_path: Path) -> None:
    path = tmp_path / "side_effects.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "codepilot.post_pr.side_effects.v1",
                "run_id": "r",
                "effects": [{"round_id": "round-001", "status": "succeeded", "metadata": {}}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="action"):
        load_side_effects(path, run_id="r")
