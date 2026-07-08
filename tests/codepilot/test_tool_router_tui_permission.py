from __future__ import annotations

import json
from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolAction
from codepilot.router.router import ToolRouter
from codepilot.trace.logger import TraceLogger
from codepilot.tui_agent.models import PermissionResponse
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

