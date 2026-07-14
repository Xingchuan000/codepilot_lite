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
    data: dict[str, Any]


@dataclass(frozen=True)
class PermissionRequestContext:
    session_id: str
    turn_id: str | None
    attempt_id: str | None
    tool_call_id: str | None


class PermissionScopeBuilder:
    """构造足够窄且可持久化的 Session 授权范围。"""

    def build(self, tool_name: str, arguments: dict[str, Any], spec: ToolSpec | None, workspace_root: Path, policy_rule: str | None) -> PermissionScope:
        if tool_name in {"replace_range", "apply_patch"}:
            value = {"tool": tool_name, "workspace": str(workspace_root.resolve())}
        elif tool_name == "run_shell":
            value = {"tool": tool_name, "command_hash": _hash_text(_normalize_command(str(arguments.get("command", ""))))}
        else:
            # 动态 MCP 工具可能不在内置 ToolSpec 注册表中。此时仍使用服务端、工具名和
            # 参数哈希组成 exact-call scope，绝不退化成仅按工具名授权。
            value = {
                "tool": tool_name,
                "server": (spec.metadata.get("server_name") if spec is not None else None) or "unknown",
                "arguments_hash": _hash_json(arguments),
                "policy_rule": policy_rule,
            }
        return PermissionScope(json.dumps(value, sort_keys=True, separators=(",", ":")), value)


class SessionPermissionBroker:
    """在 BlockingTUIBroker 外包一层 SQLite Grant/Response 持久化。"""

    def __init__(self, database: SessionDatabase, session_id: str, inner: PermissionBroker, scope_builder: PermissionScopeBuilder | None = None) -> None:
        self.store = SessionStore(database)
        self.session_id = session_id
        self.inner = inner
        self.scope_builder = scope_builder or PermissionScopeBuilder()
        self._requests: dict[str, PermissionRequest] = {}
        self._persisted_responses: set[str] = set()
        self._resolved: dict[str, PermissionResponse] = {}

    def request(self, request: PermissionRequest) -> PermissionRequest:
        self._requests[request.request_id] = request
        self.store.persist_permission_request_and_pending_call(request)
        if request.scope_key and self._has_grant(request.scope_key):
            # 已持久化 Grant 的命中是一个完整审批结果，不进入 UI 队列。缓存合成响应后，
            # Router 的同步 wait() 能取得明确批准，而不是把 inner 的 None 误判为拒绝。
            response = PermissionResponse(
                request_id=request.request_id,
                decision="approve_session",
                reason="approved by persisted session grant",
                responded_at=permission_now_iso(),
            )
            self._resolved[request.request_id] = response
            self.store.persist_permission_resolution(
                request.request_id,
                "approve_session",
                response.reason,
                create_grant=False,
                source="persisted_grant",
            )
            return request
        self.inner.request(request)
        return request

    def wait(self, request_id: str) -> PermissionResponse | None:
        if request_id in self._resolved:
            return self._resolved.pop(request_id)
        response = self.inner.wait(request_id)
        if response is not None:
            self._record_response(response, notify_inner=False)
        return response

    def resolve(self, response: PermissionResponse) -> None:
        self._record_response(response, notify_inner=True)

    def _record_response(self, response: PermissionResponse, *, notify_inner: bool) -> None:
        response_id = f"response-{response.request_id}-{response.responded_at}"
        if response_id in self._persisted_responses:
            if notify_inner:
                self.inner.resolve(response)
            return
        self.store.persist_permission_resolution(
            response.request_id,
            response.decision,
            response.reason,
            create_grant=True,
            source="ui",
        )
        self._persisted_responses.add(response_id)
        if notify_inner:
            self.inner.resolve(response)

    def restore_pending_request(self, request_id: str) -> PermissionRequest:
        record = self.store.get_permission_request(request_id)
        request = PermissionRequest(
            request_id=record.request_id,
            run_id=record.metadata.get("run_id", request_id),
            action_id=record.metadata.get("action_id"),
            tool_name=record.tool_name,
            arguments_preview=record.arguments,
            reason=record.reason,
            risk=record.metadata.get("risk"),
            side_effect=record.metadata.get("side_effect"),
            matched_rule=record.metadata.get("matched_rule"),
            created_at=record.created_at,
            status=record.status,
            session_id=record.session_id,
            turn_id=record.turn_id,
            attempt_id=record.attempt_id,
            tool_call_id=record.tool_call_id,
            scope_key=record.scope_key,
            scope_json=record.metadata.get("scope_json"),
        )
        self._requests[request_id] = request
        self.inner.request(request)
        return request

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
