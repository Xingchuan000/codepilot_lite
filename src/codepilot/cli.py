"""CodePilot Lite 命令行入口。

这里只提供手动调试工具的入口，不接 LLM，也不扩展 agent loop。
"""

import json
from typing import Literal

import typer
from pydantic import ValidationError

from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.router import ToolRouter
from codepilot.tools.base import ToolSideEffect
from codepilot.tools.registry import call_tool, call_tool_traced, find_tool_spec, list_tool_specs
from codepilot.trace.logger import TraceLogger

app = typer.Typer(add_completion=False, help="CodePilot Lite structured tools CLI.")


@app.command()
def tool(
    name: str = typer.Argument(..., help="Tool name to call."),
    args_json: str = typer.Argument(..., help="JSON encoded keyword arguments."),
    trace: bool = typer.Option(False, "--trace", help="Write this tool call to a trace file."),
    unsafe_direct: bool = typer.Option(
        False,
        "--unsafe-direct",
        help="Allow direct execution of tools with side effects without PolicyChecker.",
    ),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory for trace runs."),
    run_id: str | None = typer.Option(None, "--run-id", help="Optional existing run id."),
) -> None:
    """通过 JSON 参数直接调用一个工具。"""

    try:
        kwargs = json.loads(args_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"JSON 解析失败: {exc}", err=True)
        raise typer.Exit(1) from exc

    spec = find_tool_spec(name)
    if spec is not None and spec.side_effect != ToolSideEffect.NONE and not unsafe_direct:
        typer.echo(
            "Direct tool execution is disabled for tools with side effects. "
            "Use `codepilot route ... --approve` or pass --unsafe-direct for debugging.",
            err=True,
        )
        raise typer.Exit(1)

    if trace:
        logger = TraceLogger(runs_dir=runs_dir, run_id=run_id)
        result = call_tool_traced(name, trace_logger=logger, **kwargs)
        typer.echo(result.model_dump_json(indent=2))
        typer.echo(f"Trace written to: {logger.trace_path}")
        return

    result = call_tool(name, **kwargs)
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def route(
    action_json: str = typer.Argument(..., help="JSON encoded ToolAction."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory for trace runs."),
    run_id: str | None = typer.Option(None, "--run-id", help="Optional existing run id."),
    output_preview_chars: int = typer.Option(
        1000,
        "--output-preview-chars",
        help="Max output preview chars written to trace.",
    ),
    policy: bool = typer.Option(True, "--policy/--no-policy", help="Enable or disable PolicyChecker."),
    policy_mode: Literal["read_only", "build", "danger"] = typer.Option(
        "build",
        "--policy-mode",
        help="Policy mode.",
    ),
    approve: bool = typer.Option(False, "--approve", help="Approve actions that require approval."),
) -> None:
    """通过 ToolRouter 路由一个结构化工具 action。"""

    try:
        action_data = json.loads(action_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"JSON 解析失败: {exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        routed = ToolRouter.from_runs_dir(
            runs_dir=runs_dir,
            run_id=run_id,
            output_preview_chars=output_preview_chars,
            policy_checker=PolicyChecker.default() if policy else None,
            policy_context=PolicyContext(mode=policy_mode, approved=approve, interactive=False),
        ).route(action_data)
    except ValidationError as exc:
        typer.echo(f"ToolAction 校验失败: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(routed.model_dump_json(indent=2))
    typer.echo(f"Trace written to: {routed.trace_path}")


@app.command()
def tools() -> None:
    """列出当前注册的工具。"""

    for spec in list_tool_specs():
        typer.echo(f"{spec.name}\t{spec.risk.value}\t{spec.default_permission.value}")


if __name__ == "__main__":
    app()
