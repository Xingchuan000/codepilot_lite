"""CodePilot Lite 命令行入口。"""

import json
from pathlib import Path
from typing import Literal

import typer
from pydantic import ValidationError

from codepilot.agent.loop import MinimalAgentLoop
from codepilot.llm.fake import FakeLLMClient
from codepilot.llm.swe_agent_adapter import SweAgentModelAdapter
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.report.generator import ReportExistsError, generate_report
from codepilot.router import ToolRouter
from codepilot.tools.base import ToolSideEffect
from codepilot.tools.registry import call_tool, call_tool_traced, find_tool_spec, list_tool_specs
from codepilot.trace.logger import TraceLogger
from minisweagent.config import get_config_from_spec
from minisweagent.models import get_model
from minisweagent.utils.serialize import recursive_merge

app = typer.Typer(add_completion=False, help="CodePilot Lite structured tools CLI.")


def build_swe_model_from_config_specs(model_config: list[str], model_name: str | None = None):
    """复用 mini-SWE-agent 现有配置解析来构造模型对象。"""

    config: dict = {}
    for spec in model_config:
        config = recursive_merge(config, get_config_from_spec(spec))
    if model_name is not None:
        config = recursive_merge(config, {"model": {"model_name": model_name}})
    return get_model(config=config.get("model", {}))


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


@app.command("report")
def report_command(
    trace: str | None = typer.Option(None, "--trace", help="Path to trace.jsonl."),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id under --runs-dir."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory containing run folders."),
    output: str | None = typer.Option(None, "--output", help="Output report.md path."),
    write_json: bool = typer.Option(False, "--json", help="Also write report.json."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing report output."),
) -> None:
    """从已有 trace 生成 Evidence Report。"""

    if (trace is None) == (run_id is None):
        typer.echo("Provide exactly one of --trace or --run-id.", err=True)
        raise typer.Exit(1)

    trace_path = Path(trace) if trace is not None else Path(runs_dir) / run_id / "trace.jsonl"
    output_path = Path(output) if output is not None else None

    try:
        report_path, report = generate_report(trace_path, output_path, write_json=write_json, overwrite=overwrite)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ReportExistsError as exc:
        typer.echo(f"{exc}\nUse --overwrite to replace it.", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"Report generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Report written: {report_path}")
    typer.echo(f"Status: {report.status or 'unknown'}")
    typer.echo(f"Tests: {report.tests.status or 'unknown'}")
    typer.echo(f"Changed files: {len(report.changed_files)}")
    typer.echo(f"Policy violations: {len(report.policy.violations)}")
    if write_json:
        typer.echo(f"Report JSON written: {report_path.with_suffix('.json')}")


@app.command("agent-run")
def agent_run(
    task: str = typer.Argument(..., help="Coding task for CodePilot MinimalAgentLoop."),
    repo: str = typer.Option(".", "--repo", help="Repository path."),
    max_steps: int = typer.Option(12, "--max-steps", help="Maximum LLM loop steps."),
    policy_mode: Literal["read_only", "build", "danger"] = typer.Option("build", "--policy-mode"),
    approve: bool = typer.Option(False, "--approve", help="Approve ask tools."),
    fake_actions: str | None = typer.Option(None, "--fake-actions", help="JSONL fake LLM action responses."),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Override model name for existing mini-SWE-agent model config.",
    ),
    model_config: list[str] = typer.Option(
        [],
        "--model-config",
        help="mini-SWE-agent config spec. Can be path/name/key=value.",
    ),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory for trace runs."),
    run_id: str | None = typer.Option(None, "--run-id", help="Trace run id."),
) -> None:
    """运行第八步 MinimalAgentLoop。"""

    repo_path = Path(repo).expanduser().resolve()
    llm = (
        FakeLLMClient.from_jsonl(fake_actions)
        if fake_actions
        else SweAgentModelAdapter(model=build_swe_model_from_config_specs(model_config, model_name=model))
    )
    policy_context = PolicyContext(repo=repo_path, mode=policy_mode, approved=approve, interactive=False)
    router = ToolRouter.from_runs_dir(
        runs_dir=runs_dir,
        run_id=run_id,
        policy_checker=PolicyChecker.default(),
        policy_context=policy_context,
    )
    result = MinimalAgentLoop(llm=llm, router=router, max_steps=max_steps).run(task=task, repo=repo_path)

    typer.echo(f"Status: {result.status}")
    typer.echo(f"Success: {str(result.success).lower()}")
    typer.echo(f"Steps: {result.steps}")
    typer.echo("Changed files:")
    for path in result.changed_files:
        typer.echo(f"- {path}")
    typer.echo(f"Tests: {result.last_test_status or 'unknown'}")
    typer.echo(f"Policy violations: {result.policy_violations}")
    typer.echo(f"Trace: {result.trace_path}")
    if not result.success:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
