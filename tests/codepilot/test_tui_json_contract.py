from __future__ import annotations

import json
from pathlib import Path

from codepilot.tui import DASHBOARD_SCHEMA_VERSION
from codepilot.tui.json_output import detail_to_json_dict, dumps_dashboard_json, index_to_json_dict
from codepilot.tui.models import RunArtifactRef, RunDashboardModel, RunIndexEntry, TimelineRow


def test_index_and_detail_json_have_expected_top_level_shape() -> None:
    entry = RunIndexEntry(run_id="run-1", run_dir=Path("/tmp/run-1"), warnings=("token=abc",))
    model = RunDashboardModel(schema_version=DASHBOARD_SCHEMA_VERSION, entry=entry, timeline=(TimelineRow(step=1, event_type="run_start", title="Run started"),))

    index_payload = index_to_json_dict([entry])
    detail_payload = detail_to_json_dict(model)

    assert set(index_payload) == {"schema_version", "runs"}
    assert set(detail_payload) == {"schema_version", "run"}
    assert index_payload["runs"][0]["run_dir"] == "/tmp/run-1"
    assert index_payload["runs"][0]["warnings"] == ["[REDACTED]"]
    assert detail_payload["run"]["timeline"][0]["event_type"] == "run_start"


def test_dashboard_json_serializes_paths_and_tuples_and_is_parseable() -> None:
    entry = RunIndexEntry(run_id="run-1", run_dir=Path("/tmp/run-1"), artifacts=(RunArtifactRef(kind="trace", path=Path("/tmp/run-1/trace.jsonl"), exists=True),))
    payload = index_to_json_dict([entry])

    text = dumps_dashboard_json(payload)

    assert json.loads(text)["runs"][0]["artifacts"][0]["path"] == "/tmp/run-1/trace.jsonl"
    assert "Rich" not in text
    assert "token" not in text
    assert "password" not in text
    assert "api_key" not in text
    assert "authorization" not in text
    assert "cookie" not in text
    assert "private_key" not in text
