from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from codepilot.agent.tool_output_redaction import redact_tool_output
from codepilot.memory.policy import redact_memory_value
from codepilot.router.actions import ToolRouteResult
from codepilot.session.context_budget import estimate_tokens

VISIBLE_METADATA_KEYS = {
    "status",
    "command",
    "returncode",
    "timed_out",
    "path",
    "changed_files",
    "test_status",
    "tests",
    "diff_checked",
    "missing_evidence",
    "scope_violation_files",
    "error",
    "patch_artifact_id",
    "changed_count",
    "staged",
    "clean",
    "failed_tests",
    "has_more",
    "next_offset",
    "server_name",
    "mcp_tool_name",
    "trust_level",
}


@dataclass(frozen=True)
class PrunedToolObservation:
    tool_name: str
    content: str
    original_chars: int
    retained_chars: int
    truncated: bool
    transformed: bool
    length_truncated: bool
    strategy: str
    facts: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolOutputPruner:
    def prune(self, route_result: ToolRouteResult, *, token_budget: int) -> PrunedToolObservation:
        token_budget = max(1, token_budget)
        budget = token_budget * 4
        original = _original_observation_text(route_result)
        strategies: dict[str, tuple[str, Callable[[ToolRouteResult, int], tuple[str, dict[str, Any]]]]] = {
            "run_tests": ("pytest", _tests),
            "run_shell": ("shell", _shell),
            "read_file": ("file_read", _file_read),
            "git_diff": ("git_diff", _git_diff),
            "list_files": ("file_list", _list_files),
            "replace_range": ("write", _write),
            "apply_patch": ("write", _write),
            "write_file": ("write", _write),
            "wait_agent": ("agent_control", _agent_control),
            "list_agents": ("agent_list", _agent_list),
            "inspect_agent_patch": ("agent_patch", _agent_patch),
        }
        strategy, function = strategies.get(
            route_result.tool_name,
            ("mcp" if _is_mcp(route_result) else "fallback", _mcp if _is_mcp(route_result) else _fallback),
        )
        try:
            content, facts = function(route_result, budget)
        except (KeyError, TypeError, ValueError) as exc:
            strategy = "parse_fallback"
            content, facts = _fallback(route_result, budget)
            facts["prune_fallback_reason"] = type(exc).__name__
        content_kind = "search_result" if route_result.tool_name == "search_code" else "code" if route_result.tool_name in {"read_file", "git_diff"} else "external" if _is_mcp(route_result) else "log" if route_result.tool_name in {"run_shell", "run_tests"} else "generic"
        redacted = redact_tool_output(content, tool_name=route_result.tool_name, content_kind=content_kind)
        content = _shrink_to_estimated_token_budget(redacted.value, token_budget, preserve_lines=content_kind == "code")
        transformed = content != original
        length_truncated = estimate_tokens(original) > token_budget or "... truncated" in content
        return PrunedToolObservation(
            tool_name=route_result.tool_name,
            content=content,
            original_chars=len(original),
            retained_chars=len(content),
            truncated=transformed or length_truncated,
            transformed=transformed,
            length_truncated=length_truncated,
            strategy=strategy,
            facts=dict(redact_memory_value(facts).value),
            metadata={"redaction_count": redacted.redaction_count},
        )


def _original_observation_text(route: ToolRouteResult) -> str:
    lines = _base(route)
    metadata = route.result.metadata
    visible = {key: metadata[key] for key in sorted(VISIBLE_METADATA_KEYS) if key in metadata}
    if visible:
        lines.extend(("Important metadata:", *(f"- {key}: {value}" for key, value in visible.items())))
    if route.result.output:
        lines.extend(("Output:", route.result.output))
    return "\n".join(lines)


def _shrink_to_estimated_token_budget(content: str, token_budget: int, *, preserve_lines: bool = False) -> str:
    if estimate_tokens(content) <= token_budget:
        return content
    if preserve_lines:
        return _shrink_code_observation(content, token_budget)
    suffix = "\n... truncated; full output persisted ..."
    max_chars = max(1, token_budget * 4)
    content = f"{content[:max(0, max_chars - len(suffix))]}{suffix}"[:max_chars]
    while estimate_tokens(content) > token_budget:
        content = content[:-1]
    return content


def _shrink_code_observation(content: str, token_budget: int) -> str:
    lines = content.splitlines()
    try:
        content_index = lines.index("Content:")
    except ValueError:
        return _shrink_line_list(content, token_budget)
    prefix = lines[:content_index]
    body = lines[content_index + 1:]
    returned = next((line for line in prefix if line.startswith("Returned lines: ")), None)
    if returned is None:
        return _shrink_line_list(content, token_budget)
    match = re.search(r"Returned lines: (\d+)-(\d+)(?: \(total file lines: (\d+)\))?", returned)
    if match is None:
        return _shrink_line_list(content, token_budget)
    start, end = (int(value) for value in match.groups()[:2])
    total = int(match.group(3) or end)
    marker = "... omitted middle lines ..."
    if marker in body:
        marker_index = body.index(marker)
        head, tail = body[:marker_index], body[marker_index + 1:]
    else:
        head, tail = body, []
    while estimate_tokens(_render_code_observation(prefix, start, end, total, head, tail, marker)) > token_budget and (head or tail):
        if len(head) >= len(tail) and head:
            head.pop()
        elif tail:
            tail.pop(0)
    return _render_code_observation(prefix, start, end, total, head, tail, marker)


def _render_code_observation(prefix: list[str], start: int, end: int, total: int, head: list[str], tail: list[str], marker: str) -> str:
    omitted_start = start + len(head)
    omitted_end = end - len(tail)
    visible_head = f"{start}-{omitted_start - 1}" if head else "none"
    visible_tail = f"{omitted_end + 1}-{end}" if tail else "none"
    omitted = f"{omitted_start}-{omitted_end}" if omitted_start <= omitted_end else "none"
    path = next((line.split('path="', 1)[1].split('"', 1)[0] for line in prefix if 'To continue: read_file(path="' in line), "")
    metadata = [
        f"Returned lines: {start}-{end} (total file lines: {total})",
        f"Visible lines: {visible_head}, {visible_tail}",
        f"Omitted lines: {omitted}",
        f'To continue: read_file(path="{path}", start_line={omitted_start}, end_line={omitted_end})',
        "Content:",
        *head,
        marker,
        *tail,
    ]
    return "\n".join((*prefix[:_metadata_start(prefix)], *metadata))


def _metadata_start(prefix: list[str]) -> int:
    return next((index for index, line in enumerate(prefix) if line.startswith("Returned lines: ")), len(prefix))


def _shrink_line_list(content: str, token_budget: int) -> str:
    lines = content.splitlines()
    original_count = len(lines)
    while len(lines) > 1 and estimate_tokens("\n".join(lines)) > token_budget:
        lines.pop(len(lines) // 2)
    if len(lines) < original_count:
        lines.insert(len(lines) // 2, "... omitted lines; full output persisted ...")
        while estimate_tokens("\n".join(lines)) > token_budget and len(lines) > 1:
            marker_index = lines.index("... omitted lines; full output persisted ...")
            lines.pop(marker_index - 1 if marker_index else marker_index + 1)
    if estimate_tokens("\n".join(lines)) > token_budget:
        lines.append("... omitted lines; full output persisted ...")
    return "\n".join(lines)


def _pytest_diagnostic_lines(output: str) -> list[str]:
    """Keep high-signal pytest failure evidence, especially actual/expected values.

    ``run_tests`` already preserves pytest ``E ...`` assertion lines in its formatted
    output.  The observation pruner must not throw those lines away just because they
    do not contain the literal word ``error``.  Prefer assertion/value evidence first,
    then traceback/location/failure markers.
    """

    assertion_lines: list[str] = []
    context_lines: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^E\s+", stripped) or re.match(r"^>\s*(?:assert|raise)\b", stripped):
            assertion_lines.append(line)
            continue
        if (
            "AssertionError" in line
            or "Traceback" in line
            or line.startswith("FAILED ")
            or line.startswith("ERROR ")
            or re.search(r"(?:^|\s)[^:\s]+\.py:\d+(?::|$)", stripped)
        ):
            context_lines.append(line)

    return _deduplicate([*assertion_lines, *context_lines])


def _tests(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    metadata = route.result.metadata
    output = route.result.output
    failed = _values(metadata.get("failed_tests"))
    diagnostics = _pytest_diagnostic_lines(output)
    summaries = [line for line in output.splitlines()[-80:] if re.search(r"\b(\d+ (failed|passed|error|skipped)|failed in|passed in)\b", line, re.I)]
    lines = _base(route)
    _field(lines, "Command", metadata.get("command"))
    _field(lines, "Status", metadata.get("status"))
    _field(lines, "Return code", metadata.get("returncode"))
    if failed:
        lines.extend(("Failed tests:", *(f"- {item}" for item in failed)))
    if diagnostics:
        lines.extend(("Failure diagnostics:", *_bounded_lines(diagnostics[:24], max(160, budget // 2))))
    if summaries:
        lines.extend(("Final summary:", summaries[-1]))
    elif route.result.output_summary:
        lines.extend(("Final summary:", route.result.output_summary))
    return _limit("\n".join(lines), budget), {"failed_tests": failed, "status": metadata.get("status"), "returncode": metadata.get("returncode")}


def _shell(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    command = str(route.result.metadata.get("command", ""))
    if "pytest" in command:
        return _tests(route, budget)
    output = route.result.output.splitlines()
    errors = [line for line in output if re.search(r"(error|failed|exception|traceback|fatal)", line, re.I)]
    lines = _base(route)
    for key, label in (("command", "Command"), ("returncode", "Return code"), ("timed_out", "Timed out")):
        _field(lines, label, route.result.metadata.get(key))
    preview = [*output[:12], *(errors[:8]), *output[-12:]]
    if preview:
        lines.extend(("Output head/tail:", *_deduplicate(preview)))
    return _limit("\n".join(lines), budget), {key: route.result.metadata.get(key) for key in ("command", "returncode", "timed_out")}


def _file_read(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    metadata = route.result.metadata
    lines = _base(route)
    for key, label in (("path", "Path"), ("start_line", "Requested start line"), ("end_line", "Requested end line"), ("truncated", "Truncated")):
        _field(lines, label, metadata.get(key))
    content = route.result.output
    if len("\n".join((*lines, "Content:", content))) <= budget:
        lines.extend(("Content:", content))
    else:
        lines.extend(_fit_code_lines(content, metadata, budget - estimate_tokens("\n".join(lines))))
    return "\n".join(lines), {key: metadata.get(key) for key in ("path", "start_line", "end_line", "truncated")}


def _fit_code_lines(content: str, metadata: dict[str, Any], budget: int) -> list[str]:
    source_lines = content.splitlines()
    start = int(metadata.get("actual_start_line") or metadata.get("start_line") or 1)
    total = int(metadata.get("total_lines") or start + len(source_lines) - 1)
    if not source_lines:
        return ["Content:"]
    available = max(2, budget - 160)
    head: list[str] = []
    tail: list[str] = []
    for line in source_lines:
        if len("\n".join((*head, line, *tail))) <= available // 2:
            head.append(line)
        else:
            break
    for line in reversed(source_lines):
        if len("\n".join((*head, line, *tail))) <= available:
            tail.insert(0, line)
        else:
            break
    visible = len(head) + len(tail)
    if visible >= len(source_lines):
        return ["Content:", *source_lines]
    omitted_start = start + len(head)
    omitted_end = start + len(source_lines) - len(tail) - 1
    returned_end = start + len(source_lines) - 1
    return [
        f"Returned lines: {start}-{returned_end} (total file lines: {total})",
        f"Visible lines: {start}-{start + len(head) - 1}, {returned_end - len(tail) + 1}-{returned_end}",
        f"Omitted lines: {omitted_start}-{omitted_end}",
        f'To continue: read_file(path="{metadata.get("path", "")}", start_line={omitted_start}, end_line={omitted_end})',
        "Content:",
        *head,
        "... omitted middle lines ...",
        *tail,
    ]


def _git_diff(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    metadata = route.result.metadata
    hunks: list[str] = []
    current: list[str] = []
    for line in route.result.output.splitlines():
        if line.startswith("diff --git"):
            if current:
                hunks.extend(current[:18])
            current = [line]
        elif current and (len(current) < 18 or line.startswith("@@")):
            current.append(line)
    hunks.extend(current[:18])
    lines = _base(route)
    for key, label in (("changed_files", "Changed files"), ("changed_count", "Changed count"), ("staged", "Staged"), ("clean", "Clean")):
        _field(lines, label, metadata.get(key))
    if hunks:
        lines.extend(("Key hunks:", *hunks))
    return _limit("\n".join(lines), budget), {key: metadata.get(key) for key in ("changed_files", "changed_count", "staged", "clean")}


def _list_files(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    lines = _base(route)
    metadata = route.result.metadata
    for key in ("path", "offset", "entries_returned", "has_more", "next_offset"):
        _field(lines, key, metadata.get(key))
    if route.result.output:
        lines.extend(("Entries:", _limit(route.result.output, max(0, budget - len("\n".join(lines))))))
    return _limit("\n".join(lines), budget), {key: metadata.get(key) for key in ("path", "offset", "entries_returned", "has_more", "next_offset")}


def _write(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    lines = _base(route)
    metadata = route.result.metadata
    for key in ("changed", "changed_files", "touched_paths", "path", "patch_hash"):
        _field(lines, key, metadata.get(key))
    return _limit("\n".join(lines), budget), {key: metadata.get(key) for key in ("changed", "changed_files", "touched_paths", "path", "patch_hash")}


def _agent_control(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    metadata = route.result.metadata
    lines = _base(route)
    for key, label in (
        ("agent_id", "Agent id"),
        ("agent_type", "Agent type"),
        ("status", "Status"),
        ("test_status", "Test status"),
        ("tests", "Tests"),
        ("diff_checked", "Diff checked"),
        ("changed_files", "Changed files"),
        ("patch_artifact_id", "Patch artifact"),
        ("missing_evidence", "Missing evidence"),
        ("scope_violation_files", "Scope violation files"),
        ("error", "Error"),
    ):
        _field(lines, label, metadata.get(key))
    result = metadata.get("result")
    if isinstance(result, str) and result.strip():
        lines.extend(("Child result:", result.strip()))
    return _limit("\n".join(lines), budget), {
        key: metadata.get(key)
        for key in (
            "agent_id",
            "agent_type",
            "status",
            "test_status",
            "tests",
            "diff_checked",
            "changed_files",
            "patch_artifact_id",
            "missing_evidence",
            "scope_violation_files",
            "error",
        )
    }


def _agent_list(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    metadata = route.result.metadata
    raw_agents = metadata.get("agents")
    agents = [item for item in raw_agents if isinstance(item, dict)] if isinstance(raw_agents, list) else []

    lines = _base(route)
    running_count = sum(1 for item in agents if item.get("status") == "running")
    waiting_count = sum(1 for item in agents if item.get("status") == "waiting_permission")
    lines.append(f"Agent count: {len(agents)}")
    lines.append(f"Running agents: {running_count}")
    if waiting_count:
        lines.append(f"Waiting-permission agents: {waiting_count}")

    if agents:
        lines.append("Agents:")
        for item in agents:
            agent_id = item.get("agent_id") or item.get("child_session_id") or "unknown"
            agent_type = item.get("agent_type") or "unknown"
            status = item.get("status") or "unknown"
            write_scope = item.get("write_scope")
            parts = [f"{agent_id}", f"type={agent_type}", f"status={status}"]
            if isinstance(write_scope, list):
                parts.append(f"write_scope={write_scope}")
            test_status = item.get("test_status")
            if test_status:
                parts.append(f"test_status={test_status}")
            changed_files = item.get("changed_files")
            if isinstance(changed_files, list) and changed_files:
                parts.append(f"changed_files={changed_files}")
            patch_artifact_id = item.get("patch_artifact_id")
            if patch_artifact_id:
                parts.append(f"patch_artifact={patch_artifact_id}")
            error = item.get("error")
            if error:
                parts.append(f"error={error}")
            lines.append("- " + " | ".join(parts))

    compact_agents = [
        {
            key: item.get(key)
            for key in (
                "agent_id",
                "agent_type",
                "status",
                "write_scope",
                "test_status",
                "changed_files",
                "patch_artifact_id",
                "scope_violation_files",
                "error",
            )
            if item.get(key) not in (None, [], "")
        }
        for item in agents
    ]
    return _limit("\n".join(lines), budget), {
        "agent_count": len(agents),
        "running_count": running_count,
        "waiting_permission_count": waiting_count,
        "agents": compact_agents,
    }


def _agent_patch(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    metadata = route.result.metadata
    lines = _base(route)
    for key, label in (
        ("agent_id", "Agent id"),
        ("artifact_id", "Artifact id"),
        ("changed_files", "Changed files"),
        ("patch_truncated", "Patch truncated"),
    ):
        _field(lines, label, metadata.get(key))
    preview = metadata.get("patch_preview")
    if isinstance(preview, str) and preview:
        lines.extend(("Patch preview:", preview))
    return _limit("\n".join(lines), budget), {
        key: metadata.get(key)
        for key in ("agent_id", "artifact_id", "changed_files", "patch_truncated")
    }


def _mcp(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    lines = _base(route)
    metadata = route.result.metadata
    for key in ("server_name", "mcp_tool_name", "output_schema_validation_failed", "trust_level", "has_secret_like_content"):
        _field(lines, key, metadata.get(key))
    if route.result.output:
        lines.extend(("Structured content preview:", _head_tail(route.result.output, max(0, budget - len("\n".join(lines))))))
    return _limit("\n".join(lines), budget), {key: metadata.get(key) for key in ("server_name", "mcp_tool_name", "output_schema_validation_failed", "trust_level")}


def _fallback(route: ToolRouteResult, budget: int) -> tuple[str, dict[str, Any]]:
    lines = _base(route)
    important = {key: route.result.metadata[key] for key in ("status", "command", "returncode", "path", "changed_files") if key in route.result.metadata}
    if important:
        lines.extend(("Important metadata:", *(f"- {key}: {value}" for key, value in important.items())))
    if route.result.output:
        lines.extend(("Output head/tail:", _head_tail(route.result.output, max(0, budget - len("\n".join(lines))))))
    return _limit("\n".join(lines), budget), important


def _base(route: ToolRouteResult) -> list[str]:
    executed = route.result.metadata.get("executed")
    if executed is None:
        executed = route.success or route.result.metadata.get("policy_decision") not in {"deny", "ask"}
    lines = [f"Tool: {route.tool_name}", f"Executed: {str(bool(executed)).lower()}", f"Success: {str(route.success).lower()}"]
    if route.result.output_summary:
        lines.append(f"Summary: {route.result.output_summary}")
    if route.result.error:
        lines.append(f"Error: {route.result.error}")
    return lines


def _field(lines: list[str], label: str, value: Any) -> None:
    if value is not None:
        lines.append(f"{label}: {value}")


def _head_tail(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    half = max(0, (budget - 45) // 2)
    return f"{text[:half]}\n... truncated; full output persisted ...\n{text[-half:]}"


def _limit(text: str, budget: int) -> str:
    return text if len(text) <= budget else _head_tail(text, budget)


def _values(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def _bounded_lines(lines: list[str], budget: int) -> list[str]:
    return _limit("\n".join(lines), budget).splitlines()


def _deduplicate(lines: list[str]) -> list[str]:
    return list(dict.fromkeys(lines))


def _is_mcp(route: ToolRouteResult) -> bool:
    return bool(route.result.metadata.get("mcp") or route.result.metadata.get("server_name"))
