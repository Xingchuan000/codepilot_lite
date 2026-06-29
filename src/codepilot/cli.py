"""CodePilot Lite 命令行入口。

这里只提供手动调试工具的入口，不接 LLM，也不扩展 agent loop。
"""

import json

import typer

from codepilot.tools.registry import call_tool, list_tool_specs

app = typer.Typer(add_completion=False, help="CodePilot Lite structured tools CLI.")


@app.command()
def tool(
    name: str = typer.Argument(..., help="Tool name to call."),
    args_json: str = typer.Argument(..., help="JSON encoded keyword arguments."),
) -> None:
    """通过 JSON 参数直接调用一个工具。"""

    try:
        kwargs = json.loads(args_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"JSON 解析失败: {exc}", err=True)
        raise typer.Exit(1) from exc

    result = call_tool(name, **kwargs)
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def tools() -> None:
    """列出当前注册的工具。"""

    for spec in list_tool_specs():
        typer.echo(f"{spec.name}\t{spec.risk.value}\t{spec.default_permission.value}")


if __name__ == "__main__":
    app()
