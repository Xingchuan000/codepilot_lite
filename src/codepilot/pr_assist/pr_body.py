from __future__ import annotations

"""生成 pr_body.md。"""

import json
from pathlib import Path
from typing import Any

from codepilot.github.issue_models import IssueTask
from codepilot.pr_assist.models import PRBodyData, PRAssistSafetyGate
from codepilot.report.models import RunReport


def sanitize_markdown_text(value: str | None, *, max_chars: int = 4000) -> str:
    """做最小 Markdown 清洗，避免意外闭合代码块或输出超长内容。"""

    if not value:
        return ""
    cleaned = value.replace("\x00", "")
    cleaned = cleaned.replace("```", "`\u200b``")
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + "\n...[truncated]"
    return cleaned


def display_artifact_path(path: str | Path | None, run_dir: str | Path) -> str | None:
    """优先展示 run_dir 相对路径，避免把绝对路径直接写进 PR 材料。"""

    if path is None:
        return None
    path_obj = Path(path)
    run_dir_path = Path(run_dir).expanduser().resolve()
    try:
        return str(path_obj.expanduser().resolve().relative_to(run_dir_path))
    except Exception:
        return path_obj.name


def read_json_if_exists(path: str | Path | None) -> dict[str, Any] | None:
    """读取可选 JSON 文件；读取失败时按缺失处理。"""

    if path is None:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def build_pr_body_data(
    *,
    manifest: dict[str, Any],
    report_json: dict[str, Any] | None,
    issue_json: dict[str, Any] | None,
    pr_summary_text: str | None,
    run_dir: str | Path,
    artifacts: dict[str, Path],
    safety_gate: PRAssistSafetyGate,
) -> PRBodyData:
    """把第十一步的几个基础 artifact 整理成 PR body 所需字段。"""

    report = RunReport.model_validate(report_json) if report_json else None
    issue = IssueTask.model_validate(issue_json) if issue_json else None
    patch = manifest.get("patch") or {}
    safety_summary = manifest.get("safety_summary") or {}

    title_source = issue.title if issue else None
    title = sanitize_markdown_text(title_source or f"CodePilot changes for {manifest.get('run_id', 'run')}", max_chars=160)
    issue_ref = None
    if issue is not None:
        issue_ref = issue.ref.url or display_artifact_path(issue.ref.file_path, run_dir) or issue.title

    if report and report.final_summary:
        summary = report.final_summary
    elif pr_summary_text:
        summary = pr_summary_text[:1200]
    else:
        summary = str(manifest.get("status") or "No summary available.")

    changed_files = list(patch.get("changed_files") or (report.changed_files if report else []))
    if not changed_files and patch.get("is_empty") is True:
        changed_files = []

    tests: list[str] = []
    if report and report.tests.status:
        command = report.tests.command or "unknown"
        summary_text = report.tests.summary or ""
        tests.append(f"{command}: {report.tests.status}" + (f" — {summary_text}" if summary_text else ""))
    else:
        tests.append("unknown")

    safety_notes = [f"Safety gate: {safety_gate.status}"]
    safety_notes.extend(safety_gate.reasons)
    safety_notes.extend(safety_gate.warnings)
    if safety_summary.get("baseline_dirty") is True:
        safety_notes.append("Baseline dirty: yes")
    if patch.get("contains_preexisting_changes") is True:
        safety_notes.append("Patch may contain pre-existing changes")

    return PRBodyData(
        title=title,
        issue_ref=issue_ref,
        summary=sanitize_markdown_text(summary, max_chars=2500),
        changed_files=changed_files,
        tests=tests,
        safety_notes=safety_notes,
        report_path=display_artifact_path(artifacts.get("report_md") or artifacts.get("report_json"), run_dir),
        patch_path=display_artifact_path(artifacts.get("patch"), run_dir),
        manifest_path="artifact_manifest.json",
        restore_plan_path=display_artifact_path(artifacts.get("restore_plan"), run_dir),
        patch_sha256=patch.get("sha256"),
        patch_empty=patch.get("is_empty"),
        worktree_used=bool(manifest.get("used_worktree")),
        baseline_dirty=safety_summary.get("baseline_dirty"),
        protected_changed_files=list(patch.get("protected_changed_files") or []),
    )


def render_pr_body(data: PRBodyData, *, safety_gate: PRAssistSafetyGate) -> str:
    """把 PRBodyData 渲染成可直接提交给人工审查的 Markdown。"""

    lines: list[str] = [f"# {data.title}", ""]
    if safety_gate.status == "fail":
        lines.extend(
            [
                "> [!WARNING]",
                "> CodePilot safety gate failed. Do not create or merge this PR until the warnings are reviewed.",
                "",
            ]
        )
    if data.issue_ref:
        lines.extend(["## Issue", "", f"- {sanitize_markdown_text(data.issue_ref, max_chars=500)}", ""])
    lines.extend(["## Summary", "", sanitize_markdown_text(data.summary), "", "## Changes", ""])
    if data.changed_files:
        lines.extend(f"- `{path}`" for path in data.changed_files)
    elif data.patch_empty is True:
        lines.append("- No code changes were generated.")
    else:
        lines.append("- No changed files reported.")
    lines.extend(["", "## Tests", ""])
    lines.extend(f"- {sanitize_markdown_text(item, max_chars=500)}" for item in data.tests)
    lines.extend(["", "## Safety and Repo State", ""])
    lines.extend(f"- {sanitize_markdown_text(note, max_chars=500)}" for note in data.safety_notes)
    lines.append(f"- Worktree used: {'yes' if data.worktree_used else 'no'}")
    lines.append(
        f"- Baseline dirty: {'yes' if data.baseline_dirty else 'no' if data.baseline_dirty is False else 'unknown'}"
    )
    lines.append(f"- Patch SHA256: `{data.patch_sha256 or 'unknown'}`")
    lines.append(f"- Patch empty: {'yes' if data.patch_empty else 'no' if data.patch_empty is False else 'unknown'}")
    lines.append("- Protected changed files: " + (", ".join(f"`{p}`" for p in data.protected_changed_files) or "none"))
    lines.extend(["", "## Evidence", ""])
    lines.append(f"- Report: `{data.report_path or 'unknown'}`")
    lines.append(f"- Patch: `{data.patch_path or 'unknown'}`")
    lines.append(f"- Manifest: `{data.manifest_path or 'artifact_manifest.json'}`")
    lines.append(f"- Restore plan: `{data.restore_plan_path or 'restore_plan.md'}`")
    lines.extend(
        [
            "",
            "## Reviewer Checklist",
            "",
            "- [ ] Patch scope matches the issue",
            "- [ ] Tests are sufficient and reproducible",
            "- [ ] No protected files are included",
            "- [ ] No generated run artifacts are committed",
            "- [ ] Safety warnings have been reviewed",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_pr_body(
    data: PRBodyData,
    output_path: str | Path,
    *,
    safety_gate: PRAssistSafetyGate,
    overwrite: bool = False,
) -> Path:
    """把 PR body 写到 runs/<run_id>/pr_body.md。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_pr_body(data, safety_gate=safety_gate), encoding="utf-8")
    return path
