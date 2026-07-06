"""CodePilot Lite 命令行入口。"""

import json
import os
from pathlib import Path
from typing import Literal

import typer
from pydantic import ValidationError

from codepilot.agent.runner import run_agent_task
from codepilot.github.workflow import run_issue_workflow
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.pr_assist.models import ManifestInvalidError, PRAssistError
from codepilot.pr_assist.workflow import run_pr_assist
from codepilot.report.generator import ReportExistsError, generate_report
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

    result = run_agent_task(
        task=task,
        repo=repo,
        max_steps=max_steps,
        policy_mode=policy_mode,
        approve=approve,
        fake_actions=fake_actions,
        model=model,
        model_config=model_config,
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

    typer.echo("PR assist generated." if result.status != "blocked_by_safety" else "PR assist blocked by safety gate.")
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


if __name__ == "__main__":
    app()
