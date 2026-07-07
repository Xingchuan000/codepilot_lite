from __future__ import annotations

import json
from pathlib import Path

from codepilot.repo.git_utils import sha256_file


def write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def write_report_json(run_dir: Path, payload: dict[str, object]) -> None:
    (run_dir / "report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_artifact_manifest(run_dir: Path, artifacts: list[dict[str, object]], **extra: object) -> None:
    payload = {"schema_version": "codepilot.artifact_manifest.v1", "artifacts": artifacts, **extra}
    path = run_dir / "artifact_manifest.json"
    size_bytes = 0
    for _ in range(3):
        payload["artifacts"] = [
            {
                **item,
                "size_bytes": size_bytes if item.get("name") == "artifact_manifest" else item.get("size_bytes", 0),
            }
            for item in artifacts
        ]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        new_size_bytes = path.stat().st_size
        if new_size_bytes == size_bytes:
            break
        size_bytes = new_size_bytes


def make_success_run(runs_dir: Path, run_id: str = "run-success") -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        run_dir / "trace.jsonl",
        [
            {"schema_version": "trace.v1", "run_id": run_id, "step": 1, "event_type": "run_start", "timestamp": "2026-01-01T00:00:00+00:00", "metadata": {"task": "Fix add bug", "repo": "/tmp/repo", "source": "minimal_agent_loop"}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 2, "event_type": "policy_decision", "timestamp": "2026-01-01T00:00:01+00:00", "tool_name": "run_tests", "policy_decision": "allow", "metadata": {"approved": True, "executed": True, "requires_approval": False}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 3, "event_type": "tool_call", "timestamp": "2026-01-01T00:00:02+00:00", "tool_name": "run_tests", "success": True, "output_summary": "Tests passed", "metadata": {"status": "passed", "command": "python -m pytest", "returncode": 0}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 4, "event_type": "tool_call", "timestamp": "2026-01-01T00:00:03+00:00", "tool_name": "git_status", "success": True, "metadata": {"changed_files": ["src/calc.py"]}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 5, "event_type": "agent_finish", "timestamp": "2026-01-01T00:00:04+00:00", "success": True, "output_summary": "done", "metadata": {"status": "success", "changed_files": ["src/calc.py"]}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 6, "event_type": "run_end", "timestamp": "2026-01-01T00:00:05+00:00", "success": True, "output_summary": "done", "metadata": {}},
        ],
    )
    write_report_json(
        run_dir,
        {
            "run_id": run_id,
            "task": "Fix add bug",
            "status": "success",
            "success": True,
            "changed_files": ["src/calc.py"],
            "tests": {"status": "passed", "command": "python -m pytest", "summary": "Tests passed"},
            "policy": {"total": 1, "allowed": 1, "asked": 0, "denied": 0, "approved": 1, "violations": []},
            "diff": {"checked": True, "paths": ["src/calc.py"], "summary": "diff", "truncated": False},
            "warnings": [],
            "errors": [],
        },
    )
    write_artifact_manifest(
        run_dir,
        [
            {"name": "trace", "kind": "trace", "path": "trace.jsonl", "exists": True, "size_bytes": (run_dir / "trace.jsonl").stat().st_size, "sha256": sha256_file(run_dir / "trace.jsonl")},
            {"name": "report_json", "kind": "report_json", "path": "report.json", "exists": True, "size_bytes": (run_dir / "report.json").stat().st_size, "sha256": sha256_file(run_dir / "report.json")},
            {"name": "artifact_manifest", "kind": "artifact_manifest", "path": "artifact_manifest.json", "exists": True, "size_bytes": 0, "sha256": None},
        ],
        run_id=run_id,
        status="success",
        success=True,
        safety_summary={"baseline_dirty": False, "contains_preexisting_changes": False, "used_worktree": False},
        patch={"changed_files": ["src/calc.py"]},
    )
    return run_dir


def make_policy_denied_run(runs_dir: Path, run_id: str = "run-policy-denied") -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        run_dir / "trace.jsonl",
        [
            {"schema_version": "trace.v1", "run_id": run_id, "step": 1, "event_type": "run_start", "timestamp": "2026-01-01T00:00:00+00:00", "metadata": {"task": "Try secret"}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 2, "event_type": "policy_decision", "timestamp": "2026-01-01T00:00:01+00:00", "tool_name": "read_file", "policy_decision": "deny", "policy_reason": "Sensitive path", "metadata": {"approved": False, "executed": False, "requires_approval": False, "path": ".env"}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 3, "event_type": "run_end", "timestamp": "2026-01-01T00:00:02+00:00", "success": False, "output_summary": "failed", "metadata": {}},
        ],
    )
    write_artifact_manifest(
        run_dir,
        [{"name": "trace", "kind": "trace", "path": "trace.jsonl", "exists": True, "size_bytes": (run_dir / "trace.jsonl").stat().st_size, "sha256": sha256_file(run_dir / "trace.jsonl")}],
        run_id=run_id,
        status="failed",
        success=False,
        safety_summary={},
        patch={"changed_files": []},
    )
    return run_dir


def make_mcp_run(runs_dir: Path, run_id: str = "mcp-dashboard-demo") -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        run_dir / "trace.jsonl",
        [
            {"schema_version": "trace.v1", "run_id": run_id, "step": 1, "event_type": "run_start", "timestamp": "2026-01-01T00:00:00+00:00", "metadata": {"task": "Use MCP"}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 2, "event_type": "policy_decision", "timestamp": "2026-01-01T00:00:01+00:00", "tool_name": "mcp.filesystem.read_file", "policy_decision": "allow", "metadata": {"approved": True, "executed": True, "mcp": True, "server_name": "filesystem", "mcp_tool_name": "read_file", "descriptor_hash": "1234567890abcdef", "exposed_to_agent": True}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 3, "event_type": "tool_call", "timestamp": "2026-01-01T00:00:02+00:00", "tool_name": "mcp.filesystem.read_file", "success": True, "output_summary": "read README", "metadata": {"mcp": True, "server_name": "filesystem", "mcp_tool_name": "read_file", "descriptor_hash": "1234567890abcdef", "exposed_to_agent": True, "structured_content": {"content": "secret token=abc"}}},
            {"schema_version": "trace.v1", "run_id": run_id, "step": 4, "event_type": "run_end", "timestamp": "2026-01-01T00:00:03+00:00", "success": True, "output_summary": "done", "metadata": {}},
        ],
    )
    write_artifact_manifest(
        run_dir,
        [{"name": "trace", "kind": "trace", "path": "trace.jsonl", "exists": True, "size_bytes": (run_dir / "trace.jsonl").stat().st_size, "sha256": sha256_file(run_dir / "trace.jsonl")}],
        run_id=run_id,
        status="success",
        success=True,
        safety_summary={},
        patch={"changed_files": []},
    )
    return run_dir


def make_broken_run(runs_dir: Path, run_id: str = "run-broken") -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text('{"bad json"\n', encoding="utf-8")
    return run_dir
