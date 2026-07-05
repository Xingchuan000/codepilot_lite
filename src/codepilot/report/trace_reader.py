from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_trace_events(trace_path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    """逐行读取 trace.jsonl，并把损坏行转换成 warning。

    这里严格遵循计划，不尝试修复 JSON，只做容错跳过。
    """

    path = Path(trace_path)
    if not path.exists():
        raise FileNotFoundError(f"Trace file does not exist: {trace_path}")

    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"Line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(data, dict):
            warnings.append(f"Line {line_number}: expected JSON object, got {type(data).__name__}")
            continue
        events.append(data)
    return events, warnings
