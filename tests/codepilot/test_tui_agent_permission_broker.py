from __future__ import annotations

from codepilot.permissions import PermissionRequest, PermissionResponse
from codepilot.tui_agent.permission_broker import AutoApproveLocalWriteBroker, BlockingTUIBroker, TestBroker


def test_blocking_broker_resolve_unblocks_wait() -> None:
    broker = BlockingTUIBroker()
    request = PermissionRequest(
        request_id="perm-1",
        run_id="run-1",
        action_id="act-1",
        tool_name="replace_range",
        arguments_preview={},
        reason="need approval",
        risk="local_write",
        side_effect="local_write",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
    )

    broker.request(request)
    broker.resolve(
        PermissionResponse(
            request_id="perm-1",
            decision="approve_once",
            reason="approved",
            responded_at="2024-01-01T00:00:01Z",
        )
    )

    response = broker.wait("perm-1")

    assert response is not None
    assert response.decision == "approve_once"


def test_auto_approve_local_write_only_approves_local_write() -> None:
    inner = TestBroker()
    broker = AutoApproveLocalWriteBroker(inner)
    local_write_request = PermissionRequest(
        request_id="perm-1",
        run_id="run-1",
        action_id="act-1",
        tool_name="replace_range",
        arguments_preview={},
        reason="need approval",
        risk="local_write",
        side_effect="local_write",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
    )
    shell_request = PermissionRequest(
        request_id="perm-2",
        run_id="run-1",
        action_id="act-2",
        tool_name="run_shell",
        arguments_preview={},
        reason="need approval",
        risk="shell_execution",
        side_effect="local_exec",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
    )

    broker.request(local_write_request)
    local_write_response = broker.wait("perm-1")
    broker.request(shell_request)

    assert local_write_response is not None
    assert local_write_response.decision == "approve_once"
    assert inner.responses[0].request_id == "perm-1"
    assert len(inner.responses) == 1


def test_blocking_broker_cancel_all_denies_pending_permission() -> None:
    broker = BlockingTUIBroker()
    broker.request(
        PermissionRequest(
            request_id="perm-1",
            run_id="run-1",
            action_id="act-1",
            tool_name="replace_range",
            arguments_preview={},
            reason="need approval",
            risk="local_write",
            side_effect="local_write",
            matched_rule="tool.default_permission.ask",
            created_at="2024-01-01T00:00:00Z",
        )
    )

    broker.cancel_all("cancelled")
    response = broker.wait("perm-1")

    assert response is not None
    assert response.decision == "deny"
    assert response.reason == "cancelled"


def test_blocking_broker_clears_pending_after_wait() -> None:
    broker = BlockingTUIBroker()
    broker.request(
        PermissionRequest(
            request_id="perm-2",
            run_id="run-1",
            action_id="act-1",
            tool_name="replace_range",
            arguments_preview={},
            reason="need approval",
            risk="local_write",
            side_effect="local_write",
            matched_rule="tool.default_permission.ask",
            created_at="2024-01-01T00:00:00Z",
        )
    )
    broker.resolve(
        PermissionResponse(
            request_id="perm-2",
            decision="approve_once",
            reason="approved",
            responded_at="2024-01-01T00:00:01Z",
        )
    )

    assert broker.wait("perm-2") is not None
    assert broker.wait("perm-2") is None


def test_test_broker_clears_pending_after_wait() -> None:
    broker = TestBroker()
    broker.request(
        PermissionRequest(
            request_id="perm-3",
            run_id="run-1",
            action_id="act-1",
            tool_name="replace_range",
            arguments_preview={},
            reason="need approval",
            risk="local_write",
            side_effect="local_write",
            matched_rule="tool.default_permission.ask",
            created_at="2024-01-01T00:00:00Z",
        )
    )
    broker.resolve(
        PermissionResponse(
            request_id="perm-3",
            decision="approve_once",
            reason="approved",
            responded_at="2024-01-01T00:00:01Z",
        )
    )

    assert broker.wait("perm-3") is not None
    assert broker.wait("perm-3") is None


def test_non_interactive_broker_is_safe_to_call() -> None:
    from codepilot.tui_agent.permission_broker import NonInteractiveBroker

    broker = NonInteractiveBroker()

    assert broker.wait("perm-4") is None
    assert broker.cancel_all("cancelled") is None


def test_auto_approve_local_write_delegates_cancel_all() -> None:
    inner = TestBroker()
    broker = AutoApproveLocalWriteBroker(inner)
    request = PermissionRequest(
        request_id="perm-5",
        run_id="run-1",
        action_id="act-1",
        tool_name="run_shell",
        arguments_preview={},
        reason="need approval",
        risk="shell_execution",
        side_effect="local_exec",
        matched_rule="tool.default_permission.ask",
        created_at="2024-01-01T00:00:00Z",
    )

    broker.request(request)
    broker.cancel_all("cancelled")

    response = inner.wait("perm-5")

    assert response is not None
    assert response.decision == "deny"
