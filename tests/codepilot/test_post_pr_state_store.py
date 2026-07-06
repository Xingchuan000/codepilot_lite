from __future__ import annotations

import json
from pathlib import Path

import pytest

from codepilot.post_pr.models import PostPRRoundRef, SideEffectEntry
from codepilot.post_pr.state_store import (
    acquire_state_lock,
    append_round_to_state,
    append_side_effect,
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
    assert loaded["schema_version"] == "codepilot.post_pr.state.v1"
    round_ref = PostPRRoundRef(round_id="round-001", round_index=1, round_dir=run_dir / "post_pr/round-001")
    updated = append_round_to_state(loaded, round_ref)
    assert updated["latest_round_id"] == "round-001"
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
    assert load_side_effects(ledger_path, run_id="r")["run_id"] == "r"
