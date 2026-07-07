"""CodePilot Lite 命令行入口。"""

import json
import os
import time
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
import io
from pathlib import Path
from typing import Literal

import typer
from dataclasses import replace
from rich.console import Console
from pydantic import ValidationError

from codepilot.auto_pr.models import AutoPRError, AutoPRManifestInvalidError, AutoPRSafetyError
from codepilot.auto_pr.workflow import run_auto_pr
from codepilot.agent.runner import run_agent_task
from codepilot.github.workflow import run_issue_workflow
from codepilot.mcp.registry import MCPToolRegistry
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.post_pr.controller import run_post_pr_automation
from codepilot.pr_feedback.models import PRFeedbackError, PRFeedbackManifestInvalidError
from codepilot.pr_feedback.workflow import run_pr_feedback_loop
from codepilot.pr_assist.models import ManifestInvalidError, PRAssistError
from codepilot.pr_assist.workflow import run_pr_assist
from codepilot.tui.indexer import build_run_index
from codepilot.tui.json_output import detail_to_json_dict, dumps_dashboard_json, index_to_json_dict
from codepilot.tui.projector import build_dashboard_model
from codepilot.tui.models import RunDashboardModel, RunIndexEntry
from codepilot.tui.redaction import relative_path_for_display, relative_paths_in_text
from codepilot.tui.render import render_run_detail, render_run_index
from codepilot.report.generator import ReportExistsError, generate_report
from codepilot.router import ToolAction, ToolRouter
from codepilot.tools.base import ToolSideEffect
from codepilot.tools.registry import call_external_tool_traced, call_tool, call_tool_traced, find_tool_spec, list_tool_specs
from codepilot.trace.logger import TraceLogger

app = typer.Typer(add_completion=False, help="CodePilot Lite structured tools CLI.")


def _recorded_console() -> Console:
    return Console(file=io.StringIO(), record=True, color_system=None)


def _display_object(value: object, runs_dir: Path) -> object:
    if isinstance(value, dict):
        return {key: _display_object(item, runs_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_display_object(item, runs_dir) for item in value]
    if isinstance(value, tuple):
        return tuple(_display_object(item, runs_dir) for item in value)
    if isinstance(value, set):
        return sorted((_display_object(item, runs_dir) for item in value), key=str)
    if isinstance(value, Path):
        return Path(relative_path_for_display(value, base_dir=runs_dir))
    if isinstance(value, str):
        return relative_paths_in_text(value, base_dir=runs_dir)
    return value


def _display_run_index_entry(entry: RunIndexEntry, runs_dir: Path) -> RunIndexEntry:
    return replace(
        entry,
        run_dir=Path(relative_path_for_display(entry.run_dir, base_dir=runs_dir)),
        task=_display_object(entry.task, runs_dir),
        changed_files=tuple(relative_path_for_display(path, base_dir=runs_dir) for path in entry.changed_files),
        source_provenance=_display_object(entry.source_provenance, runs_dir),
        warnings=tuple(_display_object(warning, runs_dir) for warning in entry.warnings),
        artifacts=tuple(
            replace(
                artifact,
                path=Path(relative_path_for_display(artifact.path, base_dir=runs_dir)),
            )
            for artifact in entry.artifacts
        ),
    )


def _display_dashboard_model(model: RunDashboardModel, runs_dir: Path) -> RunDashboardModel:
    return replace(
        model,
        entry=_display_run_index_entry(model.entry, runs_dir),
        timeline=tuple(
            replace(
                row,
                output_summary=_display_object(row.output_summary, runs_dir) if row.output_summary is not None else None,
                metadata=_display_object(row.metadata, runs_dir),
            )
            for row in model.timeline
        ),
        test_summary=_display_object(model.test_summary, runs_dir),
        diff_summary=_display_object(model.diff_summary, runs_dir),
        workflow_summary=_display_object(model.workflow_summary, runs_dir),
        mcp_summary=_display_object(model.mcp_summary, runs_dir),
        warnings=tuple(_display_object(warning, runs_dir) for warning in model.warnings),
        artifact_summary=tuple(
            replace(
                artifact,
                path=Path(relative_path_for_display(artifact.path, base_dir=runs_dir)),
            )
            for artifact in model.artifact_summary
        ),
    )


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


@app.command("mcp-tools")
def mcp_tools(
    mcp_config: str = typer.Option(..., "--mcp-config", help="MCP config JSON path."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    try:
        registry = MCPToolRegistry.from_config(mcp_config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    rows = []
    for binding in registry.list_bindings():
        spec = registry.find_spec(binding.codepilot_tool_name)
        rows.append(
            {
                "name": binding.codepilot_tool_name,
                "server": binding.server_name,
                "original_tool": binding.mcp_tool_name,
                "risk": spec.risk.value if spec else None,
                "side_effect": spec.side_effect.value if spec else None,
                "default_permission": spec.default_permission.value if spec else None,
                "status": binding.status,
                "exposed_to_agent": binding.exposed_to_agent,
                "reason": binding.reason,
                "trust_level": binding.trust_level,
                "descriptor_hash": binding.descriptor_hash,
                "config_hash": binding.config_hash,
            }
        )

    if json_output:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    for row in rows:
        typer.echo(
            f"{row['name']}\t{row['risk']}\t{row['side_effect']}\t{row['default_permission']}\t{row['status']}\t"
            f"exposed={'true' if row['exposed_to_agent'] else 'false'}\treason={row['reason'] or ''}"
        )


@app.command("mcp-call")
def mcp_call(
    tool_name: str = typer.Argument(...),
    args_json: str = typer.Argument(...),
    mcp_config: str = typer.Option(..., "--mcp-config"),
    runs_dir: str = typer.Option("runs", "--runs-dir"),
    run_id: str | None = typer.Option(None, "--run-id"),
    policy: bool = typer.Option(True, "--policy/--no-policy"),
    unsafe_no_policy: bool = typer.Option(False, "--unsafe-no-policy"),
    policy_mode: Literal["read_only", "build", "danger"] = typer.Option("build", "--policy-mode"),
    approve: bool = typer.Option(False, "--approve"),
    output_preview_chars: int = typer.Option(1000, "--output-preview-chars"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"JSON 解析失败: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not isinstance(args, dict):
        typer.echo("args_json must decode to an object", err=True)
        raise typer.Exit(1)

    try:
        registry = MCPToolRegistry.from_config(mcp_config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if not registry.has_tool(tool_name):
        typer.echo(f"Unknown MCP tool: {tool_name}", err=True)
        raise typer.Exit(1)

    spec = registry.find_spec(tool_name)
    if not policy:
        if unsafe_no_policy is False and any(server.transport != "fake" for server in registry.servers.values()):
            typer.echo("--no-policy is only allowed for fake MCP transport unless --unsafe-no-policy is set.", err=True)
            raise typer.Exit(1)
        typer.echo("Warning: policy disabled; fake MCP transport only.", err=True)
        logger = TraceLogger(runs_dir=runs_dir, run_id=run_id)
        result = call_external_tool_traced(tool_name, external_registry=registry, trace_logger=logger, output_preview_chars=output_preview_chars, **args)
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "success": result.success,
                        "output": result.output,
                        "error": result.error,
                        "metadata": result.metadata,
                        "trace_path": str(logger.trace_path),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        typer.echo(f"Success: {str(result.success).lower()}")
        if spec is not None:
            typer.echo(f"Descriptor hash: {spec.metadata.get('descriptor_hash')}")
        typer.echo("Policy decision: none")
        typer.echo(f"Trace path: {logger.trace_path}")
        typer.echo(f"Output: {result.output}")
        if result.error:
            typer.echo(f"Error: {result.error}")
        if not result.success:
            raise typer.Exit(1)
        return

    router = ToolRouter.from_runs_dir(
        runs_dir=runs_dir,
        run_id=run_id,
        output_preview_chars=output_preview_chars,
        policy_checker=PolicyChecker.default(extra_tool_specs={item.name: item for item in registry.list_specs()}),
        policy_context=PolicyContext(mode=policy_mode, approved=approve, interactive=False),
        external_tool_registry=registry,
    )
    routed = router.route(ToolAction(tool_name=tool_name, arguments=args))
    if json_output:
        typer.echo(routed.model_dump_json(indent=2))
        return

    typer.echo(f"Success: {str(routed.success).lower()}")
    typer.echo(f"Policy decision: {routed.metadata.get('policy_decision')}")
    if spec is not None:
        typer.echo(f"Descriptor hash: {spec.metadata.get('descriptor_hash')}")
    typer.echo(f"Trace path: {routed.trace_path}")
    typer.echo(f"Output: {routed.result.output}")
    if routed.error:
        typer.echo(f"Error: {routed.error}")
    if not routed.success:
        raise typer.Exit(1)


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


@app.command("dashboard")
def dashboard_command(
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory containing run artifacts."),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id to inspect."),
    limit: int = typer.Option(20, "--limit", help="Maximum runs to show."),
    status: str | None = typer.Option(None, "--status", help="Optional status filter."),
    run_type: str | None = typer.Option(None, "--run-type", help="Optional run type filter."),
    json_output: bool = typer.Option(False, "--json", help="Output dashboard JSON."),
    static: bool = typer.Option(True, "--static/--tui", help="Render static Rich dashboard or interactive Textual TUI."),
    watch: bool = typer.Option(False, "--watch", help="Refresh static dashboard in the foreground."),
    watch_interval: float = typer.Option(2.0, "--watch-interval", help="Refresh interval in seconds."),
    max_timeline_rows: int = typer.Option(200, "--max-timeline-rows", help="Maximum timeline rows for detail view."),
    max_text_chars: int = typer.Option(500, "--max-text-chars", help="Maximum text chars per displayed field."),
) -> None:
    """查看 runs 目录里的只读仪表盘。"""

    runs_path = Path(runs_dir)
    if not runs_path.exists():
        typer.echo(f"runs_dir does not exist: {runs_path}", err=True)
        raise typer.Exit(1)
    if not runs_path.is_dir():
        typer.echo(f"runs_dir is not a directory: {runs_path}", err=True)
        raise typer.Exit(1)
    if watch and not static:
        typer.echo("--watch is only allowed with --static.", err=True)
        raise typer.Exit(1)
    if watch and watch_interval < 1.0:
        typer.echo("--watch-interval must be at least 1.0 seconds.", err=True)
        raise typer.Exit(1)

    def _render_once() -> None:
        if run_id is None:
            entries = tuple(_display_run_index_entry(entry, runs_path) for entry in build_run_index(runs_path, limit=limit, status=status, run_type=run_type))
            if json_output:
                typer.echo(dumps_dashboard_json(index_to_json_dict(list(entries))))
                return
            if static:
                console = _recorded_console()
                render_run_index(console, list(entries))
                typer.echo(console.export_text())
                return
            try:
                from codepilot.tui.app import create_dashboard_app
            except RuntimeError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from exc
            try:
                create_dashboard_app(runs_dir=runs_path, limit=limit, status=status, run_type=run_type).run()
            except RuntimeError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from exc
            return

        run_path = runs_path / run_id
        if not run_path.exists() or not run_path.is_dir():
            typer.echo(f"Run not found: {run_id}", err=True)
            raise typer.Exit(1)
        model = _display_dashboard_model(
            build_dashboard_model(run_path, max_timeline_rows=max_timeline_rows, max_text_chars=max_text_chars),
            runs_path,
        )
        if json_output:
            typer.echo(dumps_dashboard_json(detail_to_json_dict(model)))
            return
        if static:
            console = _recorded_console()
            render_run_detail(console, model)
            typer.echo(console.export_text())
            return
        try:
            from codepilot.tui.app import create_dashboard_app
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        try:
            create_dashboard_app(runs_dir=runs_path, limit=limit, status=status, run_type=run_type).run()
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    if not watch:
        _render_once()
        return

    while True:
        try:
            _render_once()
            time.sleep(watch_interval)
        except KeyboardInterrupt:
            return


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
    mcp_config: str | None = typer.Option(None, "--mcp-config", help="Optional MCP config JSON path."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory for trace runs."),
    run_id: str | None = typer.Option(None, "--run-id", help="Trace run id."),
) -> None:
    """运行第八步 MinimalAgentLoop。"""

    result = run_agent_task(
        task=task,
        repo=repo,
        max_steps=max_steps,
        policy_mode=policy_mode,
        approve=approve,
        fake_actions=fake_actions,
        model=model,
        model_config=model_config,
        mcp_config=mcp_config,
        runs_dir=runs_dir,
        run_id=run_id,
    )

    typer.echo(f"Status: {result.status}")
    typer.echo(f"Success: {str(result.success).lower()}")
    typer.echo(f"Steps: {result.steps}")
    typer.echo("Changed files:")
    for path in result.changed_files:
        typer.echo(f"- {path}")
    typer.echo(f"Tests: {result.last_test_status or 'unknown'}")
    typer.echo(f"Policy violations: {result.policy_violations}")
    typer.echo(f"Trace: {result.trace_path}")
    typer.echo(f"MCP config: {mcp_config or 'none'}")
    if not result.success:
        raise typer.Exit(1)


@app.command("issue")
def issue_command(
    issue_url: str | None = typer.Argument(None, help="GitHub issue URL."),
    issue_file: str | None = typer.Option(None, "--issue-file", help="Local issue markdown file."),
    repo: str = typer.Option(..., "--repo", help="Local repository path."),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id under --runs-dir."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory for run artifacts."),
    policy_mode: Literal["read_only", "build", "danger"] = typer.Option("build", "--policy-mode"),
    approve: bool = typer.Option(False, "--approve", help="Approve ask tools."),
    fake_actions: str | None = typer.Option(None, "--fake-actions", help="JSONL fake LLM action responses."),
    max_steps: int | None = typer.Option(None, "--max-steps", help="Maximum LLM loop steps."),
    report: bool = typer.Option(True, "--report/--no-report", help="Generate report.md."),
    json_report: bool = typer.Option(True, "--json-report/--no-json-report", help="Generate report.json."),
    dirty_policy: Literal["fail", "warn", "allow"] = typer.Option("fail", "--dirty-policy"),
    worktree: bool = typer.Option(False, "--worktree/--no-worktree"),
    worktree_base_dir: str | None = typer.Option(None, "--worktree-base-dir"),
    cleanup_worktree: bool = typer.Option(False, "--cleanup-worktree/--keep-worktree"),
    manifest: bool = typer.Option(True, "--manifest/--no-manifest"),
    restore_plan: bool = typer.Option(True, "--restore-plan/--no-restore-plan"),
    require_clean_source_for_worktree: bool = typer.Option(
        False,
        "--require-clean-source-for-worktree/--no-require-clean-source-for-worktree",
    ),
    worktree_branch_prefix: str = typer.Option("codepilot", "--worktree-branch-prefix"),
    redact_absolute_paths: bool = typer.Option(False, "--redact-absolute-paths/--no-redact-absolute-paths"),
    github_token_env: str = typer.Option(
        "GITHUB_TOKEN",
        "--github-token-env",
        help="Environment variable name for GitHub token.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing run artifacts."),
) -> None:
    """执行第十步 issue workflow。"""

    repo_path = Path(repo).expanduser().resolve()
    if cleanup_worktree and not worktree:
        typer.echo("cleanup_worktree requires --worktree.", err=True)
        raise typer.Exit(1)
    if worktree_base_dir is not None:
        resolved_worktree_base_dir = Path(worktree_base_dir).expanduser().resolve()
        try:
            resolved_worktree_base_dir.relative_to(repo_path)
        except ValueError:
            pass
        else:
            typer.echo("worktree_base_dir must be outside the repository.", err=True)
            raise typer.Exit(1)
    if not manifest:
        typer.echo("Warning: --no-manifest is not recommended for later automation.", err=True)
    if not restore_plan:
        typer.echo("Warning: --no-restore-plan means manual recovery info will not be generated.", err=True)
    github_token = os.environ.get(github_token_env)
    try:
        result = run_issue_workflow(
            issue_file=issue_file,
            issue_url=issue_url,
            repo=repo,
            run_id=run_id,
            runs_dir=runs_dir,
            policy_mode=policy_mode,
            approve=approve,
            fake_actions=fake_actions,
            max_steps=max_steps,
            generate_report_markdown=report,
            export_json_report=json_report,
            github_token=github_token,
            dirty_policy=dirty_policy,
            worktree=worktree,
            worktree_base_dir=worktree_base_dir,
            keep_worktree=not cleanup_worktree,
            cleanup_worktree=cleanup_worktree,
            write_manifest=manifest,
            write_restore_plan=restore_plan,
            require_clean_source_for_worktree=require_clean_source_for_worktree,
            worktree_branch_prefix=worktree_branch_prefix,
            redact_absolute_paths=redact_absolute_paths,
            overwrite=overwrite,
        )
    except (FileNotFoundError, ValueError, RuntimeError, FileExistsError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo("Issue workflow completed.")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Success: {str(bool(result.success)).lower()}")
    typer.echo(f"Repo: {result.repo_path}")
    typer.echo(f"Effective repo: {result.effective_repo_path}")
    typer.echo(f"Worktree: {'enabled' if result.used_worktree else 'disabled'}")
    typer.echo(f"Issue: {result.issue_json_path}")
    typer.echo(f"Trace: {result.trace_path}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Report JSON: {result.report_json_path}")
    typer.echo(f"Patch: {result.patch_path}")
    typer.echo(f"PR summary: {result.pr_summary_path}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Restore plan: {result.restore_plan_path}")
    typer.echo("Warnings:")
    for warning in result.warnings:
        typer.echo(f"- {warning}", err=True)
    if result.success is False or result.status in {
        "repo_safety_denied",
        "protected_patch_path_denied",
        "protected_after_path_denied",
    }:
        raise typer.Exit(1)


@app.command("pr-assist")
def pr_assist_command(
    run_dir: str | None = typer.Option(None, "--run-dir", help="Run directory containing artifact_manifest.json."),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id under --runs-dir."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory containing run folders."),
    strict_safety: bool = typer.Option(True, "--strict-safety/--no-strict-safety"),
    redact_absolute_paths: bool = typer.Option(True, "--redact-absolute-paths/--no-redact-absolute-paths"),
    include_gh_pr_command: bool = typer.Option(False, "--include-gh-pr-command"),
    github_action_template: bool = typer.Option(True, "--github-action-template/--no-github-action-template"),
    prepare_branch: bool = typer.Option(False, "--prepare-branch/--no-prepare-branch"),
    branch_prefix: str = typer.Option("codepilot", "--branch-prefix"),
    commit: bool = typer.Option(False, "--commit/--no-commit"),
    commit_message_file: str | None = typer.Option(None, "--commit-message-file"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing PR assist artifacts."),
) -> None:
    """从第十一步 artifact_manifest.json 生成人工 PR 准备材料。"""

    if (run_dir is None) == (run_id is None):
        typer.echo("Provide exactly one of --run-dir or --run-id.", err=True)
        raise typer.Exit(1)
    resolved_run_dir = (
        Path(run_dir).expanduser().resolve()
        if run_dir
        else Path(runs_dir).expanduser().resolve() / str(run_id)
    )
    try:
        result = run_pr_assist(
            run_dir=resolved_run_dir,
            strict_safety=strict_safety,
            redact_absolute_paths=redact_absolute_paths,
            include_gh_pr_command=include_gh_pr_command,
            generate_github_action_template=github_action_template,
            prepare_branch=prepare_branch,
            branch_prefix=branch_prefix,
            commit=commit,
            commit_message_file=commit_message_file,
            overwrite=overwrite,
        )
    except (FileNotFoundError, FileExistsError, ValueError, ManifestInvalidError, PRAssistError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if result.status == "blocked_by_safety":
        typer.echo("PR assist blocked by safety gate.")
    elif result.status == "manifest_invalid":
        typer.echo("PR assist manifest invalid.")
    elif result.status in {"branch_failed", "commit_failed"}:
        typer.echo("PR assist generated with local side-effect failure.")
    else:
        typer.echo("PR assist generated.")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Run dir: {result.run_dir}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Safety gate: {result.safety_gate.status}")
    typer.echo(f"PR body: {result.pr_body_path}")
    typer.echo(f"Manual commands: {result.manual_commands_path}")
    typer.echo(f"Review checklist: {result.review_checklist_path}")
    typer.echo(f"GitHub Action template: {result.github_action_template_path}")
    typer.echo(f"PR assist manifest: {result.pr_assist_manifest_path}")
    if result.branch_name:
        typer.echo(f"Local branch prepared: {result.branch_name}")
    if result.commit_sha:
        typer.echo(f"Commit prepared: {result.commit_sha}")
    typer.echo("Push executed: no")
    typer.echo("PR created: no")
    for warning in result.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    if result.status in {"manifest_invalid", "blocked_by_safety", "branch_failed", "commit_failed"}:
        raise typer.Exit(1)


@app.command("auto-pr")
def auto_pr_command(
    run_dir: str | None = typer.Option(None, "--run-dir", help="Run directory containing pr_assist_manifest.json."),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id under --runs-dir."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory containing run folders."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    execute: bool = typer.Option(False, "--execute"),
    allow_push: bool = typer.Option(False, "--allow-push"),
    allow_create_pr: bool = typer.Option(False, "--allow-create-pr"),
    allow_comment: bool = typer.Option(False, "--allow-comment"),
    allow_empty_pr: bool = typer.Option(False, "--allow-empty-pr"),
    remote_name: str = typer.Option("origin", "--remote-name"),
    base_branch: str | None = typer.Option(None, "--base-branch"),
    head_branch: str | None = typer.Option(None, "--head-branch"),
    repo_slug: str | None = typer.Option(None, "--repo-slug"),
    token_env: str = typer.Option("GITHUB_TOKEN", "--token-env"),
    draft: bool = typer.Option(True, "--draft/--ready-for-review"),
    controlled_action_template: bool = typer.Option(
        True,
        "--controlled-action-template/--no-controlled-action-template",
    ),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """执行第十三步 Controlled Auto PR workflow。"""

    if (run_dir is None) == (run_id is None):
        typer.echo("Provide exactly one of --run-dir or --run-id.", err=True)
        raise typer.Exit(1)
    if not execute and not dry_run:
        typer.echo("--no-dry-run requires --execute.", err=True)
        raise typer.Exit(1)
    resolved_run_dir = (
        Path(run_dir).expanduser().resolve()
        if run_dir
        else Path(runs_dir).expanduser().resolve() / str(run_id)
    )
    try:
        result = run_auto_pr(
            run_dir=resolved_run_dir,
            dry_run=False if execute else dry_run,
            execute=execute,
            allow_push=allow_push,
            allow_create_pr=allow_create_pr,
            allow_comment=allow_comment,
            allow_empty_pr=allow_empty_pr,
            remote_name=remote_name,
            base_branch=base_branch,
            head_branch=head_branch,
            repo_slug=repo_slug,
            token_env=token_env,
            draft=draft,
            generate_workflow_template=controlled_action_template,
            overwrite=overwrite,
        )
    except (FileNotFoundError, FileExistsError, ValueError, AutoPRManifestInvalidError, AutoPRSafetyError, AutoPRError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if execute and result.status == "pr_created":
        typer.echo("Controlled Auto PR completed.")
    else:
        typer.echo("Controlled Auto PR plan generated.")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Mode: {'execute' if execute else 'dry-run'}")
    typer.echo(f"Safety gate: {result.safety_gate.status}")
    typer.echo(f"Head branch: {result.branch_push_plan.remote_branch if result.branch_push_plan else 'unknown'}")
    typer.echo(f"Base branch: {result.branch_push_plan.base_branch if result.branch_push_plan else 'unknown'}")
    if result.branch_push_plan and result.push_executed:
        typer.echo(f"Pushed branch: {result.branch_push_plan.remote_branch}")
    if result.branch_push_plan:
        typer.echo(f"Commit: {result.branch_push_plan.commit_sha}")
    if result.pr_result and result.pr_result.url:
        typer.echo(f"PR created: {result.pr_result.url}")
    else:
        typer.echo(f"PR created: {'yes' if result.pr_created else 'no'}")
    typer.echo(f"Push executed: {'yes' if result.push_executed else 'no'}")
    typer.echo(f"GitHub API called: {'yes' if result.github_api_called else 'no'}")
    typer.echo(f"Comment posted: {'yes' if result.comment_posted else 'no'}")
    typer.echo(f"Plan: {result.auto_pr_plan_path}")
    typer.echo(f"Manifest: {result.auto_pr_manifest_path}")
    typer.echo(f"Controlled workflow: {result.controlled_workflow_path}")
    for warning in result.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    if result.status in {"manifest_invalid", "failed"}:
        raise typer.Exit(1)
    if execute and result.status == "blocked_by_safety":
        raise typer.Exit(1)
    if execute and not allow_push:
        raise typer.Exit(1)


@app.command("pr-feedback")
def pr_feedback_command(
    run_dir: str | None = typer.Option(None, "--run-dir", help="Run directory containing auto_pr_manifest.json."),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id under --runs-dir."),
    runs_dir: str = typer.Option("runs", "--runs-dir", help="Directory containing run folders."),
    auto_pr_manifest: str | None = typer.Option(None, "--auto-pr-manifest", help="Path to auto_pr_manifest.json."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    execute: bool = typer.Option(False, "--execute"),
    wait_ci: bool = typer.Option(False, "--wait-ci"),
    poll_interval_seconds: int = typer.Option(30, "--poll-interval-seconds"),
    timeout_seconds: int = typer.Option(900, "--timeout-seconds"),
    include_logs: bool = typer.Option(True, "--include-logs/--no-include-logs"),
    include_success_logs: bool = typer.Option(False, "--include-success-logs"),
    max_log_bytes: int = typer.Option(200_000, "--max-log-bytes"),
    max_feedback_items: int = typer.Option(20, "--max-feedback-items"),
    max_followup_rounds: int = typer.Option(1, "--max-followup-rounds"),
    allow_run_agent: bool = typer.Option(False, "--allow-run-agent"),
    allow_push_update: bool = typer.Option(False, "--allow-push-update"),
    allow_comment: bool = typer.Option(False, "--allow-comment"),
    repo_slug: str | None = typer.Option(None, "--repo-slug"),
    pull_number: int | None = typer.Option(None, "--pull-number"),
    head_branch: str | None = typer.Option(None, "--head-branch"),
    token_env: str = typer.Option("GITHUB_TOKEN", "--token-env"),
    feedback_action_template: bool = typer.Option(True, "--feedback-action-template/--no-feedback-action-template"),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """执行第十四步 PR feedback / PR review loop。"""

    if (run_dir is None) == (run_id is None):
        typer.echo("Provide exactly one of --run-dir or --run-id.", err=True)
        raise typer.Exit(1)
    if not execute and not dry_run:
        typer.echo("--no-dry-run requires --execute.", err=True)
        raise typer.Exit(1)
    if allow_push_update and not allow_run_agent:
        typer.echo("--allow-push-update requires --allow-run-agent.", err=True)
        raise typer.Exit(1)
    if allow_run_agent and not execute:
        typer.echo("Warning: --allow-run-agent has no effect without --execute.", err=True)
    if allow_push_update and not execute:
        typer.echo("Warning: --allow-push-update has no effect without --execute.", err=True)
    resolved_run_dir = (
        Path(run_dir).expanduser().resolve()
        if run_dir
        else Path(runs_dir).expanduser().resolve() / str(run_id)
    )
    try:
        result = run_pr_feedback_loop(
            run_dir=resolved_run_dir,
            auto_pr_manifest_path=auto_pr_manifest,
            dry_run=dry_run,
            execute=execute,
            wait_ci=wait_ci,
            include_logs=include_logs,
            include_success_logs=include_success_logs,
            allow_run_agent=allow_run_agent,
            allow_push_update=allow_push_update,
            allow_comment=allow_comment,
            max_feedback_items=max_feedback_items,
            max_log_bytes=max_log_bytes,
            max_followup_rounds=max_followup_rounds,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
            token_env=token_env,
            repo_slug=repo_slug,
            pull_number=pull_number,
            head_branch=head_branch,
            feedback_action_template=feedback_action_template,
            overwrite=overwrite,
        )
    except (FileNotFoundError, FileExistsError, ValueError, PRFeedbackManifestInvalidError, PRFeedbackError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    checks_summary = {
        "success": sum(1 for check in result.checks if check.conclusion == "success"),
        "failure": sum(1 for check in result.checks if check.conclusion == "failure"),
        "pending": sum(1 for check in result.checks if check.conclusion == "pending"),
    }
    typer.echo("PR feedback loop completed.")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Mode: {'execute' if execute else 'dry-run'}")
    typer.echo(f"PR: {result.pr.url if result.pr else 'n/a'}")
    typer.echo(
        "Checks: "
        f"{checks_summary['failure']} failed, {checks_summary['success']} passed, {checks_summary['pending']} pending"
    )
    typer.echo(f"Feedback items: {len(result.feedback_items)}")
    head_stale = "unknown"
    if result.feedback_freshness is not None:
        head_stale = "yes" if result.feedback_freshness.is_stale else "no"
    typer.echo(f"Head stale: {head_stale}")
    typer.echo(f"Agent ran: {'yes' if result.agent_ran else 'no'}")
    typer.echo(f"Follow-up patch generated: {'yes' if result.patch_generated else 'no'}")
    typer.echo(f"Commit created: {'yes' if result.commit_created else 'no'}")
    typer.echo(f"PR branch updated: {'yes' if result.push_update_executed else 'no'}")
    typer.echo(f"Comment posted: {'yes' if result.comment_posted else 'no'}")
    typer.echo(f"Report: {result.ci_feedback_report_path}")
    typer.echo(f"Follow-up task: {result.followup_task_path}")
    typer.echo(f"Update plan: {result.pr_update_plan_path}")
    typer.echo(f"Manifest: {result.ci_feedback_manifest_path}")
    typer.echo(f"Feedback workflow: {result.feedback_workflow_path}")
    for warning in result.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    if result.status in {"blocked", "failed"}:
        raise typer.Exit(1)
    if execute and result.api_degraded:
        raise typer.Exit(1)


@app.command("post-pr")
def post_pr_command(
    run_dir: str | None = typer.Option(None, "--run-dir"),
    run_id: str | None = typer.Option(None, "--run-id"),
    runs_dir: str = typer.Option("runs", "--runs-dir"),
    auto_pr_manifest: str | None = typer.Option(None, "--auto-pr-manifest"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    execute: bool = typer.Option(False, "--execute"),
    max_rounds: int = typer.Option(2, "--max-rounds"),
    wait_ci: bool = typer.Option(False, "--wait-ci"),
    poll_interval_seconds: int = typer.Option(30, "--poll-interval-seconds"),
    timeout_seconds: int = typer.Option(900, "--timeout-seconds"),
    include_logs: bool = typer.Option(True, "--include-logs/--no-include-logs"),
    include_success_logs: bool = typer.Option(False, "--include-success-logs"),
    max_log_bytes: int = typer.Option(200_000, "--max-log-bytes"),
    max_feedback_items: int = typer.Option(20, "--max-feedback-items"),
    stop_on_repeated_feedback: bool = typer.Option(True, "--stop-on-repeated-feedback/--no-stop-on-repeated-feedback"),
    approval_file: str | None = typer.Option(None, "--approval-file"),
    approve_run_agent: bool = typer.Option(False, "--approve-run-agent"),
    approve_push_update: bool = typer.Option(False, "--approve-push-update"),
    approve_comment: bool = typer.Option(False, "--approve-comment"),
    resume: bool = typer.Option(False, "--resume"),
    token_env: str = typer.Option("GITHUB_TOKEN", "--token-env"),
    post_pr_action_template: bool = typer.Option(True, "--post-pr-action-template/--no-post-pr-action-template"),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """执行第十五步 Post-PR automation。"""

    if (run_dir is None) == (run_id is None):
        typer.echo("Provide exactly one of --run-dir or --run-id.", err=True)
        raise typer.Exit(1)
    if not execute and not dry_run:
        typer.echo("--no-dry-run requires --execute.", err=True)
        raise typer.Exit(1)
    if approve_push_update and not approve_run_agent and not resume:
        typer.echo("--approve-push-update requires --approve-run-agent or --resume.", err=True)
        raise typer.Exit(1)
    if approve_run_agent and not execute:
        typer.echo("Warning: --approve-run-agent has no effect without --execute.", err=True)
    if approve_push_update and not execute:
        typer.echo("Warning: --approve-push-update has no effect without --execute.", err=True)
    if approve_comment and not execute:
        typer.echo(
            "Note: --approve-comment also controls whether post_comment enters the dry-run approval request.",
            err=True,
        )
    if max_rounds < 1 or max_rounds > 3:
        typer.echo("max_rounds must be between 1 and 3.", err=True)
        raise typer.Exit(1)

    resolved_run_dir = Path(run_dir).expanduser().resolve() if run_dir else Path(runs_dir).expanduser().resolve() / str(run_id)
    effective_dry_run = False if execute else dry_run
    try:
        result = run_post_pr_automation(
            run_dir=resolved_run_dir,
            auto_pr_manifest_path=auto_pr_manifest,
            dry_run=effective_dry_run,
            execute=execute,
            max_rounds=max_rounds,
            wait_ci=wait_ci,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
            token_env=token_env,
            include_logs=include_logs,
            include_success_logs=include_success_logs,
            max_log_bytes=max_log_bytes,
            max_feedback_items=max_feedback_items,
            stop_on_repeated_feedback=stop_on_repeated_feedback,
            approve_run_agent=approve_run_agent,
            approve_push_update=approve_push_update,
            approve_comment=approve_comment,
            approval_file=approval_file,
            resume=resume,
            overwrite=overwrite,
            post_pr_action_template=post_pr_action_template,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo("Post-PR automation completed.")
    typer.echo(f"Run ID: {result.run_id}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Terminal reason: {result.terminal_reason}")
    typer.echo(f"Rounds: {len(result.rounds)} / {max_rounds}")
    typer.echo(f"Mode: {'execute' if execute else 'dry-run'}")
    typer.echo(f"Agent ran: {'yes' if any(item.agent_ran for item in result.rounds) else 'no'}")
    typer.echo(f"PR branch updated: {'yes' if any(item.push_update_executed for item in result.rounds) else 'no'}")
    typer.echo(f"Comment posted: {'yes' if any(item.comment_posted for item in result.rounds) else 'no'}")
    typer.echo(f"Approval request: {result.approval_request_path or 'n/a'}")
    typer.echo(f"Manifest: {result.manifest_path}")
    typer.echo(f"Report: {result.report_path}")
    typer.echo(f"Workflow: {result.workflow_path or 'n/a'}")
    if result.status in {"blocked", "failed"} or result.terminal_reason in {"state_locked", "manifest_invalid", "stale_approval", "approval_expired", "push_failed", "agent_failed"}:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
