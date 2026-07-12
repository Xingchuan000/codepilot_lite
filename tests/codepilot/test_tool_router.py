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


def test_tool_router_routes_list_files_offset_page(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for index in range(5):
        (repo / f"file_{index}.txt").write_text("hi\n", encoding="utf-8")
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs", run_id="run-test")

    routed = router.route(
        ToolAction(
            tool_name="list_files",
            arguments={"repo": repo, "path": ".", "max_depth": 1, "max_entries": 2, "offset": 2},
        )
    )

    assert routed.success is True
    assert routed.result.output.splitlines() == ["file_2.txt", "file_3.txt"]
    assert routed.result.metadata["has_more"] is True
    assert routed.result.metadata["next_offset"] == 4
    assert routed.metadata["arguments_keys"] == ["max_depth", "max_entries", "offset", "path", "repo"]


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


def test_tool_router_without_policy_keeps_original_trace_shape(tmp_path: Path) -> None:
    router = ToolRouter.from_runs_dir(runs_dir=tmp_path / "runs", run_id="run-test")

    routed = router.route(ToolAction(tool_name="run_shell", arguments={"repo": tmp_path, "command": "echo hi"}))

    assert routed.success is True
    lines = (tmp_path / "runs" / "run-test" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_type"] for line in lines] == ["tool_call"]
