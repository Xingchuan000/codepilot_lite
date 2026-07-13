from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4


def _make_prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def make_project_id() -> str:
    return _make_prefixed_id("proj")


def make_session_id() -> str:
    return _make_prefixed_id("sess")


def make_turn_id() -> str:
    return _make_prefixed_id("turn")


def make_attempt_id() -> str:
    return _make_prefixed_id("att")


def make_message_id() -> str:
    return _make_prefixed_id("msg")


def make_part_id() -> str:
    return _make_prefixed_id("part")


def make_tool_call_id() -> str:
    return _make_prefixed_id("call")


def make_tool_result_id() -> str:
    return _make_prefixed_id("result")


def make_event_id() -> str:
    return _make_prefixed_id("evt")


def make_artifact_id() -> str:
    return _make_prefixed_id("art")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
