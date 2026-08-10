from __future__ import annotations

import json
from typing import Any
from uuid import uuid4


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str) -> Any:
    return json.loads(value)


def bool_to_int(value: bool) -> int:
    return int(value)


def local_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"
