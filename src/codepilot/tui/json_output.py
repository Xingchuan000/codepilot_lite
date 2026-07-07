from __future__ import annotations

import json
from typing import Any

from codepilot.tui import DASHBOARD_SCHEMA_VERSION
from codepilot.tui.models import RunDashboardModel, RunIndexEntry
from codepilot.tui.redaction import redact_value


def index_to_json_dict(entries: list[RunIndexEntry]) -> dict[str, Any]:
    return {"schema_version": DASHBOARD_SCHEMA_VERSION, "runs": [redact_value(entry.to_json_dict()) for entry in entries]}


def detail_to_json_dict(model: RunDashboardModel) -> dict[str, Any]:
    return {"schema_version": DASHBOARD_SCHEMA_VERSION, "run": redact_value(model.to_json_dict())}


def dumps_dashboard_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
