"""工具注册表。

这里把工具函数和 ToolSpec 统一映射起来，后续路由层可以直接复用。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from codepilot.tools.base import DefaultPermission, ToolResult, ToolRisk, ToolSideEffect, ToolSpec
from codepilot.tools.file_tools import list_files, read_file
from codepilot.tools.search_tools import search_code
from codepilot.tools.shell_tools import run_shell
from codepilot.trace.events import TraceEvent
from codepilot.trace.logger import TraceLogger

ToolFn = Callable[..., ToolResult]

TOOL_SPECS: dict[str, ToolSpec] = {
    "list_files": ToolSpec(
        name="list_files",
        description="List files and directories under a repository path.",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        parameters={
            "repo": "仓库根路径（字符串或 Path）。",
            "path": "相对于 repo 的目录路径，默认为当前目录。",
            "max_depth": "最大递归深度，超过此深度的条目不会被返回。",
            "include_hidden": "是否包含隐藏文件与目录（以 . 开头）。",
            "max_entries": "最多返回的条目数，超出则截断。",
        },
    ),
    "read_file": ToolSpec(
        name="read_file",
        description="Read a file snippet with line numbers.",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        parameters={
            "repo": "仓库根路径（字符串或 Path）。",
            "path": "相对于 repo 的文件路径。",
            "start_line": "起始行号（1 起算）。",
            "end_line": "结束行号（包含在内）。",
            "max_chars": "输出最大字符数，超出则截断。",
        },
    ),
    "search_code": ToolSpec(
        name="search_code",
        description="Search code text under a repository path.",
        risk=ToolRisk.READ_ONLY,
        side_effect=ToolSideEffect.NONE,
        default_permission=DefaultPermission.ALLOW,
        parameters={
            "repo": "仓库根路径（字符串或 Path）。",
            "query": "搜索关键词或正则片段。",
            "path": "相对于 repo 的搜索目录路径。",
            "file_glob": "文件名匹配模式，默认 *。",
            "max_results": "最多返回的匹配条数。",
            "case_sensitive": "是否区分大小写，默认不区分。",
        },
    ),
    "run_shell": ToolSpec(
        name="run_shell",
        description="Run a shell command inside the repository root.",
        risk=ToolRisk.SHELL_EXECUTION,
        side_effect=ToolSideEffect.LOCAL_EXEC,
        default_permission=DefaultPermission.ASK,
        parameters={
            "repo": "仓库根路径（字符串或 Path）。",
            "command": "要执行的 shell 命令。",
            "timeout": "命令超时秒数，默认 30。",
            "max_output_chars": "输出最大字符数，超出则截断。",
        },
    ),
}

TOOL_FUNCTIONS: dict[str, ToolFn] = {
    "list_files": list_files,
    "read_file": read_file,
    "search_code": search_code,
    "run_shell": run_shell,
}

SENSITIVE_INPUT_KEY_PARTS = (
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
)


def _preview_output(output: str, max_chars: int = 1000) -> tuple[str, bool]:
    """生成 trace 使用的输出预览，避免写入过长内容。"""

    if len(output) <= max_chars:
        return output, False
    suffix = "... truncated"
    return f"{output[: max(0, max_chars - len(suffix))]}{suffix}", True


def _is_sensitive_input_key(key: str) -> bool:
    """判断输入字段名是否明显包含敏感信息。"""

    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_INPUT_KEY_PARTS)


def _redact_trace_input(value: Any) -> Any:
    """把输入整理成适合 trace 记录的结构。"""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_input_key(str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_trace_input(item)
        return redacted

    if isinstance(value, list):
        return [_redact_trace_input(item) for item in value]

    if isinstance(value, tuple):
        return [_redact_trace_input(item) for item in value]

    if isinstance(value, set):
        return sorted(_redact_trace_input(item) for item in value)

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, str | int | float | bool) or value is None:
        return value

    return repr(value)


def get_tool_spec(name: str) -> ToolSpec:
    """按名字获取工具说明。"""

    return TOOL_SPECS[name]


def list_tool_specs() -> list[ToolSpec]:
    """列出全部工具说明。"""

    return list(TOOL_SPECS.values())


def call_tool(name: str, **kwargs: Any) -> ToolResult:
    """按名字调用工具，并把参数错误统一收敛成 ToolResult。"""

    if name not in TOOL_FUNCTIONS:
        return ToolResult(success=False, error=f"Unknown tool: {name}")

    try:
        return TOOL_FUNCTIONS[name](**kwargs)
    except TypeError as exc:
        return ToolResult(success=False, error=f"Invalid arguments for tool {name}: {exc}")
    except Exception as exc:
        return ToolResult(success=False, error=f"Tool {name} failed: {exc}")


def call_tool_traced(
    name: str,
    trace_logger: TraceLogger,
    output_preview_chars: int = 1000,
    **kwargs: Any,
) -> ToolResult:
    """调用工具并把结果写入 trace。"""

    spec = TOOL_SPECS.get(name)
    result = call_tool(name, **kwargs)

    output_preview, preview_truncated = _preview_output(result.output, output_preview_chars)
    metadata = dict(result.metadata)
    metadata["output_chars"] = len(result.output)
    metadata["output_preview_truncated"] = preview_truncated

    event = TraceEvent(
        run_id=trace_logger.run_id,
        step=trace_logger.next_step,
        event_type="tool_call",
        tool_name=name,
        risk=spec.risk.value if spec else None,
        side_effect=spec.side_effect.value if spec else None,
        default_permission=spec.default_permission.value if spec else None,
        input=_redact_trace_input(kwargs),
        success=result.success,
        output_summary=result.output_summary,
        output_preview=output_preview,
        error=result.error,
        metadata=metadata,
    )
    trace_logger.record(event)
    return result
