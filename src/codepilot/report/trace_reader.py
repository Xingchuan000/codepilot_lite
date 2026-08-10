from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from codepilot.trace.events import TraceEvent

CURRENT_TRACE_SCHEMA_VERSION = "trace.v1"


def read_trace_events(trace_path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = Path(trace_path)
    if not path.exists():
        raise FileNotFoundError(f"Trace file does not exist: {trace_path}")

    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"expected JSON object, got {type(payload).__name__}")
            if payload.get("schema_version") != CURRENT_TRACE_SCHEMA_VERSION:
                raise ValueError(f"unsupported schema_version: {payload.get('schema_version')!r}")
            event = TraceEvent.model_validate_json(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"Line {line_number}: invalid JSON: {exc.msg}")
            continue
        except (ValidationError, ValueError) as exc:
            warnings.append(f"Line {line_number}: invalid current trace event: {exc}")
            continue
        events.append(event.model_dump(mode="json"))
    return events, warnings
