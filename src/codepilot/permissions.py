from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4


PermissionDecision = Literal["approve_once", "deny"]
PermissionRequestStatus = Literal["pending", "approved", "denied", "expired"]


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    run_id: str
    action_id: str | None
    tool_name: str
    arguments_preview: dict[str, Any]
    reason: str
    risk: str | None
    side_effect: str | None
    matched_rule: str | None
    created_at: str
    status: PermissionRequestStatus = "pending"


@dataclass(frozen=True)
class PermissionResponse:
    request_id: str
    decision: PermissionDecision
    reason: str | None
    responded_at: str


class PermissionBroker(Protocol):
    def request(self, request: PermissionRequest) -> PermissionRequest: ...

    def wait(self, request_id: str) -> PermissionResponse | None: ...

    def resolve(self, response: PermissionResponse) -> None: ...

    def cancel_all(self, reason: str = "cancelled") -> None: ...


def make_permission_request_id() -> str:
    return f"perm-{uuid4().hex[:12]}"


def permission_now_iso() -> str:
    return datetime.now(UTC).isoformat()
