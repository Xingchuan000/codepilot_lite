from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from codepilot.llm.types import ChatMessage
from codepilot.tools.base import ToolSpec

SYSTEM_PROMPT = """You are CodePilot Lite, a coding agent operating on a local repository.

Do not reveal hidden chain-of-thought.

For repository actions, use the provided native tools. Do not write tool calls as JSON, XML,
DSML, Markdown code blocks, or pseudo tool syntax in assistant text.

For ordinary conversation or explanation that requires no repository action, answer with
natural assistant text.

Before editing a file, inspect the relevant code first. Do not claim a file was modified unless
a write tool actually succeeded. Respect tool permissions and write scopes.

Only use write tools when the user explicitly requests a modification or explicitly authorizes
modifications in the current conversation. Discovering that a change would be useful is not
authorization.

If a tool reports a validation or execution error, correct the arguments and call the tool again
when appropriate. Use the exact argument types and enum values from the tool schema; do not
invent aliases or substitute strings for arrays, numbers, or enum values.

For repository tasks that require structured completion evidence, call codepilot_finish after the
work is complete.
"""

PRIMARY_AGENT_CONTROL_GUIDANCE = """## Delegated-agent result handling

When a delegated child is used, consume its structured result instead of repeating its work.
If wait_agent reports status=completed together with test_status=passed, diff_checked=true,
missing_evidence=[], and a patch_artifact_id, treat those fields as the child execution evidence.
If the user asked to inspect without applying, call inspect_agent_patch once and then report the
child result; do not enter the child worktree or rerun the same tests unless wait_agent or
inspect_agent_patch reports failed, incomplete, missing, or contradictory evidence.

Treat list_agents as authoritative runtime state for delegated children. If one list_agents
observation shows multiple children with status=running at the same time, that is sufficient
evidence that they coexisted concurrently; do not inspect worktree timestamps, session storage,
or Git metadata solely to prove concurrency.
"""


def build_system_prompt(
    extra_tool_specs: Sequence[ToolSpec] | None = None,
    *,
    tool_specs: Sequence[ToolSpec] | None = None,
    agent_instructions: str | None = None,
) -> str:
    """Build the one Native Tool Calling system prompt."""

    sections = [SYSTEM_PROMPT]
    visible_specs = tuple(tool_specs or ()) + tuple(extra_tool_specs or ())
    if any(spec.name == "spawn_agent" for spec in visible_specs):
        sections.append(PRIMARY_AGENT_CONTROL_GUIDANCE)
    if agent_instructions:
        sections.append("## Current agent role\n" + agent_instructions.strip())
    return "\n\n".join(sections)


def build_user_prompt(task: str, repo: str | Path) -> str:
    return (
        f"Task: {task}\n"
        f"Repository: {Path(repo)}\n"
        "Remember: omit repo in tool arguments unless necessary; the loop will inject the current repo."
    )


def build_initial_messages(
    task: str,
    repo: str | Path,
    *,
    extra_tool_specs: Sequence[ToolSpec] | None = None,
    tool_specs: Sequence[ToolSpec] | None = None,
    agent_instructions: str | None = None,
) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="system",
            content=build_system_prompt(
                extra_tool_specs=extra_tool_specs,
                tool_specs=tool_specs,
                agent_instructions=agent_instructions,
            ),
        ),
        ChatMessage(role="user", content=build_user_prompt(task, repo)),
    ]


def build_system_event_text(event_type: str, payload: dict[str, Any]) -> str:
    return f"Session event: {event_type}\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
