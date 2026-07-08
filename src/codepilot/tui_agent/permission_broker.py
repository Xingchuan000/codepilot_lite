from __future__ import annotations

import queue
import threading
from uuid import uuid4

from codepilot.tui_agent.models import PermissionRequest, PermissionResponse
from codepilot.tui_agent.session_store import now_iso


class PermissionBroker:
    def request(self, request: PermissionRequest) -> PermissionRequest:
        raise NotImplementedError

    def resolve(self, response: PermissionResponse) -> None:
        raise NotImplementedError


class NonInteractiveBroker(PermissionBroker):
    def request(self, request: PermissionRequest) -> PermissionRequest:
        return request

    def resolve(self, response: PermissionResponse) -> None:
        return None


class TestBroker(PermissionBroker):
    __test__ = False

    def __init__(self) -> None:
        self.requests: list[PermissionRequest] = []
        self.responses: list[PermissionResponse] = []
        self._pending: dict[str, queue.Queue[PermissionResponse]] = {}
        self._resolved_early: dict[str, PermissionResponse] = {}

    def request(self, request: PermissionRequest) -> PermissionRequest:
        self.requests.append(request)
        pending = queue.Queue(maxsize=1)
        early = self._resolved_early.pop(request.request_id, None)
        if early is not None:
            pending.put(early)
        self._pending[request.request_id] = pending
        return request

    def resolve(self, response: PermissionResponse) -> None:
        self.responses.append(response)
        pending = self._pending.get(response.request_id)
        if pending is not None:
            try:
                pending.put_nowait(response)
            except queue.Full:
                pass
            return
        self._resolved_early[response.request_id] = response

    def cancel_all(self, reason: str = "cancelled") -> None:
        for request_id in list(self._pending.keys()) + list(self._resolved_early.keys()):
            self._resolved_early[request_id] = PermissionResponse(
                request_id=request_id,
                decision="deny",
                reason=reason,
                responded_at=now_iso(),
            )
            pending = self._pending.get(request_id)
            if pending is None:
                continue
            try:
                pending.put_nowait(self._resolved_early[request_id])
            except queue.Full:
                pass

    def wait(self, request_id: str) -> PermissionResponse | None:
        pending = self._pending.get(request_id)
        if pending is None:
            return None
        try:
            return pending.get()
        finally:
            self._pending.pop(request_id, None)
            self._resolved_early.pop(request_id, None)


class BlockingTUIBroker(PermissionBroker):
    def __init__(self) -> None:
        self._pending: dict[str, queue.Queue[PermissionResponse]] = {}
        self._resolved_early: dict[str, PermissionResponse] = {}
        self._lock = threading.Lock()

    def request(self, request: PermissionRequest) -> PermissionRequest:
        with self._lock:
            pending = queue.Queue(maxsize=1)
            early = self._resolved_early.pop(request.request_id, None)
            if early is not None:
                pending.put(early)
            self._pending[request.request_id] = pending
        return request

    def resolve(self, response: PermissionResponse) -> None:
        with self._lock:
            pending = self._pending.get(response.request_id)
        if pending is not None:
            try:
                pending.put_nowait(response)
            except queue.Full:
                pass
            return
        with self._lock:
            self._resolved_early[response.request_id] = response

    def cancel_all(self, reason: str = "cancelled") -> None:
        with self._lock:
            request_ids = list(self._pending.keys()) + list(self._resolved_early.keys())
            for request_id in request_ids:
                self._resolved_early[request_id] = PermissionResponse(
                    request_id=request_id,
                    decision="deny",
                    reason=reason,
                    responded_at=now_iso(),
                )
                pending = self._pending.get(request_id)
                if pending is None:
                    continue
                try:
                    pending.put_nowait(self._resolved_early[request_id])
                except queue.Full:
                    pass

    def wait(self, request_id: str) -> PermissionResponse | None:
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return None
        try:
            return pending.get()
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
                self._resolved_early.pop(request_id, None)


class AutoApproveLocalWriteBroker(PermissionBroker):
    def __init__(self, inner: PermissionBroker) -> None:
        self.inner = inner

    def request(self, request: PermissionRequest) -> PermissionRequest:
        self.inner.request(request)
        if request.side_effect == "local_write":
            self.resolve(
                PermissionResponse(
                    request_id=request.request_id,
                    decision="approve_once",
                    reason="auto-approved local_write",
                    responded_at=request.created_at,
                )
            )
        return request

    def resolve(self, response: PermissionResponse) -> None:
        self.inner.resolve(response)

    def wait(self, request_id: str) -> PermissionResponse | None:
        return getattr(self.inner, "wait", lambda _request_id: None)(request_id)

    def cancel_all(self, reason: str = "cancelled") -> None:
        cancel_all = getattr(self.inner, "cancel_all", None)
        if callable(cancel_all):
            cancel_all(reason)


def make_permission_request_id() -> str:
    return f"perm-{uuid4().hex[:12]}"
