from __future__ import annotations

"""生成 manual_pr_commands.md。"""

import shlex
from pathlib import Path

from codepilot.pr_assist.models import ManualCommandPlan, PRAssistSafetyGate


FORBIDDEN_COMMAND_SNIPPETS = [
    "git push",
    "gh pr create",
    "reset --hard",
    "clean -fd",
    "stash --include-untracked",
    "checkout -- .",
]


def quote(value: str | Path) -> str:
    """统一使用 shell 安全引号。"""

    return shlex.quote(str(value))


def repo_arg(repo_path: str | Path | None, *, redact_absolute_paths: bool) -> str:
    """决定命令中 repo 参数应展示真实路径还是占位符。"""

    if repo_path is None:
        return "<repo>"
    if redact_absolute_paths:
        return "<repo>"
    return quote(repo_path)


def artifact_arg(path: str | Path, *, run_dir: str | Path, redact_absolute_paths: bool) -> str:
    """尽量在文档里展示 run_dir 下的稳定位置，避免泄露真实绝对路径。"""

    path_obj = Path(path)
    run_dir_path = Path(run_dir).expanduser().resolve()
    try:
        display = path_obj.expanduser().resolve().relative_to(run_dir_path)
        if redact_absolute_paths:
            return quote(Path("runs") / run_dir_path.name / display)
        return quote(run_dir_path / display)
    except Exception:
        return quote(path_obj.name)


def render_manual_pr_commands(
    *,
    run_id: str,
    repo_path: str | Path | None,
    effective_repo_path: str | Path | None,
    run_dir: str | Path,
    patch_path: str | Path | None,
    branch_name: str,
    safety_gate: PRAssistSafetyGate,
    include_gh_pr_command: bool = False,
    redact_absolute_paths: bool = True,
) -> ManualCommandPlan:
    """按计划生成只面向人工执行的命令说明，不直接做 push / PR。"""

    repo = repo_arg(effective_repo_path or repo_path, redact_absolute_paths=redact_absolute_paths)
    pr_body = artifact_arg(Path(run_dir) / "pr_body.md", run_dir=run_dir, redact_absolute_paths=redact_absolute_paths)
    checklist = artifact_arg(
        Path(run_dir) / "review_checklist.md",
        run_dir=run_dir,
        redact_absolute_paths=redact_absolute_paths,
    )
    patch = artifact_arg(patch_path or Path(run_dir) / "changes.patch", run_dir=run_dir, redact_absolute_paths=redact_absolute_paths)

    commands: list[str] = [
        "# Manual PR Commands",
        "",
        "## 1. Review generated artifacts",
        "",
        "```bash",
        f"cat {pr_body}",
        f"cat {checklist}",
        f"git -C {repo} status --short",
        "```",
        "",
    ]
    warnings: list[str] = []
    if redact_absolute_paths:
        warnings.append("Absolute repo path was redacted. Replace <repo> before running commands.")

    if safety_gate.status == "fail":
        commands.extend(
            [
                "## 2. Safety gate failed",
                "",
                "No apply, branch, commit, push, or PR creation command is generated for this run.",
                "",
            ]
        )
        return ManualCommandPlan(commands=commands, warnings=warnings)

    commands.extend(
        [
            "## 2. Inspect patch",
            "",
            "```bash",
            f"git -C {repo} apply --check {patch}",
            "```",
            "",
            "## 3. Create a local branch manually",
            "",
            "```bash",
            f"git -C {repo} switch -c {quote(branch_name)}",
            "```",
            "",
            "## 4. Apply patch manually",
            "",
            "```bash",
            f"git -C {repo} apply --index {patch}",
            "```",
            "",
            "## 5. Run tests manually",
            "",
            "```bash",
            "# Use the commands listed in pr_body.md / report.md",
            "```",
            "",
            "## 6. Commit manually",
            "",
            "```bash",
            f"git -C {repo} commit -m {quote('Fix: review CodePilot generated patch')}",
            "```",
            "",
        ]
    )
    pr_create_included = False
    if include_gh_pr_command:
        commands.extend(
            [
                "## Optional: create PR manually",
                "",
                "```bash",
                f"# gh pr create --fill --body-file {pr_body}",
                "```",
                "",
            ]
        )
        pr_create_included = True

    text = "\n".join(commands)
    forbidden = [item for item in FORBIDDEN_COMMAND_SNIPPETS if item in text and item != "gh pr create"]
    if forbidden:
        raise ValueError(f"Forbidden command snippet generated: {forbidden}")
    return ManualCommandPlan(
        commands=commands,
        warnings=warnings,
        destructive_commands_included=False,
        push_commands_included=False,
        pr_create_commands_included=pr_create_included,
    )


def write_manual_pr_commands(plan: ManualCommandPlan, output_path: str | Path, *, overwrite: bool = False) -> Path:
    """把人工命令文档写到磁盘。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(plan.commands).rstrip() + "\n", encoding="utf-8")
    return path
