from __future__ import annotations

from codepilot.permissions import PermissionRequest, PermissionResponse
from codepilot.tui_agent.permission_broker import BlockingTUIBroker, TestBroker


def _request(request_id: str) -> PermissionRequest:
    return PermissionRequest(
        request_id=request_id,
        run_id="run-1",
        action_id="act-1",
        tool_name="run_shell",
        arguments_preview={},
        reason="need approval",
        risk="shell_execution",
        side_effect="local_exec",
        external_impact="unknown",
        reversibility="unknown",
        matched_rule="effect.approval.ask",
        created_at="2024-01-01T00:00:00Z",
    )


def test_blocking_broker_resolve_unblocks_wait() -> None:
    broker = BlockingTUIBroker()
    broker.request(_request("perm-1"))
    broker.resolve(PermissionResponse("perm-1", "approve_once", "approved", "2024-01-01T00:00:01Z"))

    response = broker.wait("perm-1")

    assert response is not None
    assert response.decision == "approve_once"


def test_blocking_broker_cancel_all_denies_pending_permission() -> None:
    broker = BlockingTUIBroker()
    broker.request(_request("perm-1"))
    broker.cancel_all("cancelled")

    response = broker.wait("perm-1")

    assert response is not None
    assert response.decision == "deny"
    assert response.reason == "cancelled"


def test_blocking_broker_clears_pending_after_wait() -> None:
    broker = BlockingTUIBroker()
    broker.request(_request("perm-2"))
    broker.resolve(PermissionResponse("perm-2", "approve_once", "approved", "2024-01-01T00:00:01Z"))

    assert broker.wait("perm-2") is not None
    assert broker.wait("perm-2") is None


def test_test_broker_clears_pending_after_wait() -> None:
    broker = TestBroker()
    broker.request(_request("perm-3"))
    broker.resolve(PermissionResponse("perm-3", "approve_once", "approved", "2024-01-01T00:00:01Z"))

    assert broker.wait("perm-3") is not None
    assert broker.wait("perm-3") is None


def test_non_interactive_broker_is_safe_to_call() -> None:
    from codepilot.tui_agent.permission_broker import NonInteractiveBroker

    broker = NonInteractiveBroker()

    assert broker.wait("perm-4") is None
    assert broker.cancel_all("cancelled") is None
