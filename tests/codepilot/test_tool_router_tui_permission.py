from __future__ import annotations

import json
from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.permissions import PermissionResponse
from codepilot.router import ToolAction
from codepilot.router.router import ToolRouter
from codepilot.trace.logger import TraceLogger
from codepilot.tui_agent.permission_broker import BlockingTUIBroker


def _trace_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_router_registers_permission_before_trace_publish_and_executes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    broker = BlockingTUIBroker()

    def hook(event) -> None:
        if event.event_type == "permission_request":
            broker.resolve(
                PermissionResponse(
                    request_id=event.permission_request_id,
                    decision="approve_once",
                    reason="approved",
                    responded_at=event.timestamp,
                )
            )

    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-1", record_hook=hook)
    router = ToolRouter.from_runs_dir(
        trace_logger=logger,
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(repo=repo, mode="build", approved=False, interactive=True),
        permission_broker=broker,
    )

    result = router.route(ToolAction(tool_name="run_shell", arguments={"repo": str(repo), "command": "echo hi"}))

    assert result.success is True
    assert "hi" in result.result.output
    assert [event["event_type"] for event in _trace_events(logger.trace_path)] == [
        "policy_decision",
        "permission_request",
        "permission_response",
        "tool_call",
    ]


def test_router_deny_does_not_execute_tool(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    broker = BlockingTUIBroker()

    def hook(event) -> None:
        if event.event_type == "permission_request":
            broker.resolve(
                PermissionResponse(
                    request_id=event.permission_request_id,
                    decision="deny",
                    reason="denied",
                    responded_at=event.timestamp,
                )
            )

    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-1", record_hook=hook)
    router = ToolRouter.from_runs_dir(
        trace_logger=logger,
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(repo=repo, mode="build", approved=False, interactive=True),
        permission_broker=broker,
    )

    result = router.route(ToolAction(tool_name="run_shell", arguments={"repo": str(repo), "command": "echo hi"}))

    assert result.success is False
    assert [event["event_type"] for event in _trace_events(logger.trace_path)] == [
        "policy_decision",
        "permission_request",
        "permission_response",
    ]


def test_router_accepts_structural_permission_broker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    class StructuredBroker:
        def __init__(self) -> None:
            self.requested = None

        def request(self, request):
            self.requested = request
            return request

        def wait(self, request_id: str):
            return PermissionResponse(
                request_id=request_id,
                decision="approve_once",
                reason="approved",
                responded_at="2024-01-01T00:00:01Z",
            )

        def resolve(self, response) -> None:
            return None

        def cancel_all(self, reason: str = "cancelled") -> None:
            return None

    broker = StructuredBroker()
    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-1")
    router = ToolRouter.from_runs_dir(
        trace_logger=logger,
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(repo=repo, mode="build", approved=False, interactive=True),
        permission_broker=broker,
    )

    result = router.route(ToolAction(tool_name="run_shell", arguments={"repo": str(repo), "command": "echo hi"}))

    assert result.success is True
    assert broker.requested is not None


def test_router_raises_when_broker_missing_wait(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    class NoWaitBroker:
        def request(self, request):
            return request

        def resolve(self, response) -> None:
            return None

        def cancel_all(self, reason: str = "cancelled") -> None:
            return None

    logger = TraceLogger(runs_dir=tmp_path / "runs", run_id="run-1")
    router = ToolRouter.from_runs_dir(
        trace_logger=logger,
        policy_checker=PolicyChecker.default(),
        policy_context=PolicyContext(repo=repo, mode="build", approved=False, interactive=True),
        permission_broker=NoWaitBroker(),
    )

    try:
        router.route(ToolAction(tool_name="run_shell", arguments={"repo": str(repo), "command": "echo hi"}))
    except AttributeError:
        return
    raise AssertionError("router should fail when permission broker has no wait()")
