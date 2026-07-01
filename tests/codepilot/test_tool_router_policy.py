import json
from pathlib import Path

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolAction, ToolRouter


def _router(tmp_path: Path, context: PolicyContext | None = None) -> ToolRouter:
    return ToolRouter.from_runs_dir(
        runs_dir=tmp_path / "runs",
        run_id="run-test",
        policy_checker=PolicyChecker.default(),
        policy_context=context,
    )


def test_tool_router_policy_allows_and_traces_decision(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    router = _router(tmp_path)

    routed = router.route(ToolAction(tool_name="list_files", arguments={"repo": tmp_path, "path": "."}))

    assert routed.success is True
    assert routed.metadata["policy_decision"] == "allow"
    assert routed.metadata["executed"] is True

    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]


def test_tool_router_policy_denies_sensitive_paths_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(ToolAction(tool_name="read_file", arguments={"repo": tmp_path, "path": ".env"}))

    assert routed.success is False
    assert routed.result.metadata["policy_decision"] == "deny"
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_tool_router_policy_denies_dangerous_commands_without_tool_call(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(ToolAction(tool_name="run_shell", arguments={"repo": tmp_path, "command": "rm -rf ."}))

    assert routed.success is False
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_tool_router_policy_requires_approval_when_not_approved(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(ToolAction(tool_name="run_shell", arguments={"repo": tmp_path, "command": "echo hi"}))

    assert routed.success is False
    assert routed.metadata["requires_approval"] is True
    assert routed.result.metadata["requires_approval"] is True
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision"]


def test_tool_router_policy_executes_approved_ask_actions(tmp_path: Path) -> None:
    router = _router(tmp_path, context=PolicyContext(mode="build", approved=True))

    routed = router.route(ToolAction(tool_name="run_shell", arguments={"repo": tmp_path, "command": "echo hi"}))

    assert routed.success is True
    assert routed.metadata["policy_decision"] == "ask"
    assert routed.metadata["approved"] is True
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]


def test_tool_router_policy_route_many_keeps_step_order(tmp_path: Path) -> None:
    router = _router(tmp_path)

    router.route_many(
        [
            {"tool_name": "list_files", "arguments": {"repo": tmp_path, "path": "."}},
            {"tool_name": "read_file", "arguments": {"repo": tmp_path, "path": ".env"}},
        ],
        record_run_events=False,
    )

    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["step"] for line in lines] == [1, 2, 3]


def test_tool_router_policy_allows_unknown_tools_to_reach_registry(tmp_path: Path) -> None:
    router = _router(tmp_path)

    routed = router.route(ToolAction(tool_name="unknown", arguments={"repo": tmp_path}))

    assert routed.success is False
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["policy_decision", "tool_call"]
