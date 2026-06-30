"""CodePilot Lite 命令行入口。

这里只提供手动调试工具的入口，不接 LLM，也不扩展 agent loop。
"""

import json

import typer
from pydantic import ValidationError

from codepilot.router import ToolRouter
from codepilot.tools.registry import call_tool, call_tool_traced, list_tool_specs
from codepilot.trace.logger import TraceLogger

app = typer.Typer(add_completion=False, help="CodePilot Lite structured tools CLI.")


@app.command()
def tool(
    name: str = typer.Argument(..., help="Tool name to call."),
    args_json: str = typer.Argument(..., help="JSON encoded keyword arguments."),
    trace: bool = typer.Option(False, "--trace", help="Write this tool call to a trace file."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory for trace runs."),
    run_id: str | None = typer.Option(None, "--run-id", help="Optional existing run id."),
) -> None:
    """通过 JSON 参数直接调用一个工具。"""

    try:
        kwargs = json.loads(args_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"JSON 解析失败: {exc}", err=True)
        raise typer.Exit(1) from exc

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
