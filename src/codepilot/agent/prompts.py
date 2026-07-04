from __future__ import annotations

from pathlib import Path

from codepilot.llm.types import ChatMessage
from codepilot.tools.base import ToolSpec
from codepilot.tools.registry import list_tool_specs

SYSTEM_PROMPT_HEADER = """You are CodePilot Lite, a coding agent operating on a local repository.
Return exactly one JSON object on every turn.
Do not output Markdown.
Do not output multiple actions.
Do not reveal hidden chain-of-thought.
The only allowed action types are tool_call and finish.
Prefer structured tools over free-form shell access.
run_shell is only a fallback.
Before making an edit, you must read_file or search_code first.
After making changes, you must run_tests.
Before finish, you must inspect git_status or git_diff.
If a policy deny happens, do not repeat the same unsafe action.
The repo argument may be omitted because the loop will inject the current repo.
IMPORTANT JSON FIELD RULES:
For tool calls, use exactly these standard keys:
{"type":"tool_call","tool_name":"<one registered tool name>","arguments":{}}
When replacement text spans multiple lines, keep the necessary trailing newline in "replacement".
Do NOT use these non-standard keys in your final answer:
- "tool"
- "parameters"
- "action"
- "name"
- "input"
- "args"
For finish, use exactly:
{"type":"finish","status":"success","summary":"...","tests":"...","changed_files":[]}
GOOD:
{"type":"tool_call","tool_name":"list_files","arguments":{"path":"."}}
BAD:
{"action":"list_files","parameters":{"path":"."}}
BAD:
{"type":"tool_call","tool":"list_files","parameters":{"path":"."}}
"""


def _truncate_parameter_description(value: object, max_chars: int = 160) -> str:
    """压缩工具参数说明，避免 system prompt 被无关长文本淹没。"""

    text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - len('... truncated')]}... truncated"


def render_tool_catalog(specs: list[ToolSpec] | None = None, *, include_repo_parameter: bool = False) -> str:
    """把工具注册表渲染为稳定、短小的文本目录。"""

    catalog_specs = specs or list_tool_specs()
    lines = ["Available tools:"]
    for spec in sorted(catalog_specs, key=lambda item: item.name):
        lines.append(f"- name: {spec.name}")
        lines.append(f"  description: {spec.description}")
        lines.append(f"  risk: {spec.risk.value}")
        lines.append(f"  side_effect: {spec.side_effect.value}")
        lines.append(f"  default_permission: {spec.default_permission.value}")
        lines.append("  parameters:")
        for parameter_name, description in spec.parameters.items():
            if parameter_name == "repo" and not include_repo_parameter:
                continue
            lines.append(f"    - {parameter_name}: {_truncate_parameter_description(description)}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    """构建给模型的 system prompt。"""

    return f"{SYSTEM_PROMPT_HEADER}\n{render_tool_catalog()}"


def build_user_prompt(task: str, repo: str | Path) -> str:
    """构建首轮 user prompt。"""

    return (
        f"Task: {task}\n"
        f"Repository: {Path(repo)}\n"
        "Remember: omit repo in tool arguments unless necessary; the loop will inject it."
    )


def build_initial_messages(task: str, repo: str | Path) -> list[ChatMessage]:
    """构建 loop 首轮消息。"""

    return [
        ChatMessage(role="system", content=build_system_prompt()),
        ChatMessage(role="user", content=build_user_prompt(task, repo)),
    ]
