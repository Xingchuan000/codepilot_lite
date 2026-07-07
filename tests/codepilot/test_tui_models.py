from __future__ import annotations

from pathlib import Path

from codepilot.tui import DASHBOARD_SCHEMA_VERSION
from codepilot.tui.models import RunArtifactRef, RunDashboardModel, RunIndexEntry, TimelineRow


def test_run_artifact_ref_to_json_dict_converts_path() -> None:
    payload = RunArtifactRef(kind="trace", path=Path("/tmp/run/trace.jsonl"), exists=True).to_json_dict()

    assert payload["path"] == "/tmp/run/trace.jsonl"


def test_run_index_entry_defaults_are_dashboard_friendly() -> None:
    entry = RunIndexEntry()

    assert entry.status == "unknown"
    assert entry.schema_version == DASHBOARD_SCHEMA_VERSION


def test_run_dashboard_model_to_json_dict_converts_tuples_to_lists() -> None:
    model = RunDashboardModel(
        schema_version=DASHBOARD_SCHEMA_VERSION,
        entry=RunIndexEntry(run_id="run-1"),
        timeline=(TimelineRow(step=1, event_type="run_start", title="Run started"),),
        warnings=("a", "b"),
    )

    payload = model.to_json_dict()

    assert payload["timeline"] == [{"step": 1, "event_type": "run_start", "title": "Run started", "status": None, "category": "event", "tool_name": None, "policy_decision": None, "executed": None, "risk": None, "output_summary": None, "metadata": {}}]
    assert payload["warnings"] == ["a", "b"]


def test_timeline_row_supports_category_and_executed_fields() -> None:
    row = TimelineRow(step=3, event_type="policy_decision", title="Policy decision", category="policy", executed=False)

    assert row.category == "policy"
    assert row.executed is False


def test_unknown_artifact_kind_serializes() -> None:
    payload = RunArtifactRef(kind="mystery", path=Path("x"), exists=False, warnings=("w",)).to_json_dict()

    assert payload["kind"] == "mystery"
    assert payload["warnings"] == ["w"]
