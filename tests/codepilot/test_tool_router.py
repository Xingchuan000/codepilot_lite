import json
from pathlib import Path

from codepilot.router import ToolAction, ToolRouter


def test_tool_router_routes_one_action_and_writes_trace(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs", run_id="run-test")

    routed = router.route(ToolAction(tool_name="list_files", arguments={"repo": tmp_path, "path": "."}))

    assert routed.success is True
    assert routed.metadata["run_id"] == "run-test"
    assert routed.metadata["arguments_keys"] == ["path", "repo"]
    assert routed.trace_path == str(tmp_path / "runs" / "run-test" / "trace.jsonl")

    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["tool_name"] == "list_files"


def test_tool_router_route_many_records_run_events_and_keeps_going(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs", run_id="run-test")

    results = router.route_many(
        [
            {"tool_name": "list_files", "arguments": {"repo": tmp_path, "path": "."}},
            {"tool_name": "unknown", "arguments": {"repo": tmp_path}},
        ],
        task="demo",
    )

    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False

    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["run_start", "tool_call", "tool_call", "run_end"]
    assert json.loads(lines[0])["metadata"]["source"] == "tool_router"
    assert json.loads(lines[-1])["success"] is False
