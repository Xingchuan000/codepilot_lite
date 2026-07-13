from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codepilot.permissions import PermissionBroker, PermissionRequest, PermissionResponse, permission_now_iso
from codepilot.session.database import SessionDatabase
from codepilot.session.store import SessionStore
from codepilot.tools.base import ToolSpec


@dataclass(frozen=True)
class PermissionScope:
    key: str


class PermissionScopeBuilder:
    """构造足够窄且可持久化的 Session 授权范围。"""

    def build(self, tool_name: str, arguments: dict[str, Any], spec: ToolSpec, workspace_root: Path, policy_rule: str | None) -> PermissionScope:
        if tool_name in {"replace_range", "apply_patch"}:
            value = {"tool": tool_name, "workspace": str(workspace_root.resolve())}
        elif tool_name == "run_shell":
            value = {"tool": tool_name, "command_hash": _hash_text(_normalize_command(str(arguments.get("command", ""))))}
        else:
            value = {"tool": tool_name, "server": spec.metadata.get("server_name"), "arguments_hash": _hash_json(arguments), "policy_rule": policy_rule}
        return PermissionScope(json.dumps(value, sort_keys=True, separators=(",", ":")))


class SessionPermissionBroker:
    """在 BlockingTUIBroker 外包一层 SQLite Grant/Response 持久化。"""

    def __init__(self, database: SessionDatabase, session_id: str, inner: PermissionBroker, scope_builder: PermissionScopeBuilder | None = None) -> None:
        self.store = SessionStore(database)
        self.session_id = session_id
        self.inner = inner
        self.scope_builder = scope_builder or PermissionScopeBuilder()
        self._requests: dict[str, PermissionRequest] = {}
        self._persisted_responses: set[str] = set()

    def request(self, request: PermissionRequest) -> PermissionRequest:
        scope_key = request.scope_key
        self._requests[request.request_id] = request
        self.store.create_permission_request(
            request_id=request.request_id,
            session_id=self.session_id,
            turn_id=request.turn_id,
            attempt_id=request.attempt_id,
            tool_call_id=request.tool_call_id,
            scope_key=scope_key,
            tool_name=request.tool_name,
            arguments=request.arguments_preview,
            reason=request.reason,
            status="pending",
            created_at=request.created_at,
        )
        if scope_key and self._has_grant(scope_key):
            response = PermissionResponse(request.request_id, "approve_session", "approved by session grant", permission_now_iso())
            self.resolve(response)
            return request
        self.inner.request(request)
        return request

    def wait(self, request_id: str) -> PermissionResponse | None:
        response = self.inner.wait(request_id)
        if response is not None:
            self.resolve(response)
        return response

    def resolve(self, response: PermissionResponse) -> None:
        request = self._requests.get(response.request_id)
        response_id = f"response-{response.request_id}-{response.responded_at}"
        if response_id in self._persisted_responses:
            self.inner.resolve(response)
            return
        self.store.create_permission_response(
            response_id=response_id,
            request_id=response.request_id,
            decision=response.decision,
            reason=response.reason,
            responded_at=response.responded_at,
        )
        self._persisted_responses.add(response_id)
        if request is not None and response.decision == "approve_session" and request.scope_key:
            self.store.create_permission_grant(session_id=self.session_id, scope_key=request.scope_key)
        self.inner.resolve(response)

    def cancel_all(self, reason: str = "cancelled") -> None:
        self.inner.cancel_all(reason)

    def _has_grant(self, scope_key: str) -> bool:
        with self.store.database.transaction() as connection:
            return connection.execute("SELECT 1 FROM permission_grants WHERE session_id = ? AND scope_key = ? AND revoked_at IS NULL LIMIT 1", (self.session_id, scope_key)).fetchone() is not None


def _normalize_command(command: str) -> str:
    return " ".join(shlex.split(command))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_text(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")))
