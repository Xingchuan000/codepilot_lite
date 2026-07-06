from __future__ import annotations

"""生成 review_checklist.md。"""

from pathlib import Path
from typing import Any

from codepilot.pr_assist.models import PRBodyData, PRAssistSafetyGate


def render_review_checklist(
    *,
    manifest: dict[str, Any],
    pr_body_data: PRBodyData,
    safety_gate: PRAssistSafetyGate,
) -> str:
    """根据 manifest 与 pr_body 数据生成人工复核清单。"""

    patch = manifest.get("patch") or {}
    safety_summary = manifest.get("safety_summary") or {}
    lines = ["# PR Review Checklist", ""]
    if safety_gate.status == "fail":
        lines.extend(
            [
                "> [!WARNING]",
                "> Safety gate failed. Do not create a PR before reviewing and resolving the warnings below.",
                "",
            ]
        )
    lines.extend(
        [
            "## Patch Scope",
            "",
            "- [ ] Changed files match the issue scope",
            "- [ ] Patch is not empty unless expected",
            "- [ ] No run artifacts are included in the patch",
            "",
            "## Safety",
            "",
            "- [ ] Repo safety did not fail",
            "- [ ] No protected files are included",
            "- [ ] No secrets or token-like strings appear in generated PR materials",
            "- [ ] Baseline dirty warning has been reviewed",
            "",
            "## Tests",
            "",
            "- [ ] Relevant tests passed",
            "- [ ] Failed / skipped tests are explained",
            "- [ ] Test command is reproducible locally",
            "",
            "## Worktree / Cleanup",
            "",
            "- [ ] If worktree was used, inspect effective repo",
            "- [ ] If worktree should be removed, follow restore_plan.md",
            "- [ ] Original repo state is understood",
            "",
            "## PR Body",
            "",
            "- [ ] Summary accurately reflects actual changes",
            "- [ ] Evidence links point to generated artifacts",
            "- [ ] No full diff or sensitive logs are pasted into PR body",
            "",
            "## Generated Facts",
            "",
            f"- Safety gate: `{safety_gate.status}`",
            f"- Worktree used: `{'yes' if manifest.get('used_worktree') else 'no'}`",
            f"- Baseline dirty: `{'yes' if safety_summary.get('baseline_dirty') else 'no' if safety_summary.get('baseline_dirty') is False else 'unknown'}`",
            f"- Patch empty: `{'yes' if patch.get('is_empty') else 'no' if patch.get('is_empty') is False else 'unknown'}`",
        ]
    )
    if pr_body_data.tests == ["unknown"] or any("unknown" in item for item in pr_body_data.tests):
        lines.append("- Test status is unknown. Run relevant tests manually before opening a PR.")
    if safety_summary.get("baseline_dirty") is True:
        lines.append("- Baseline was dirty. Confirm generated patch does not include unrelated user changes.")
    if manifest.get("used_worktree") is True:
        lines.append("- Agent used an isolated worktree. Confirm whether it should be kept or removed.")
    for reason in safety_gate.reasons:
        lines.append(f"- Safety reason: {reason}")
    for warning in safety_gate.warnings:
        lines.append(f"- Safety warning: {warning}")
    return "\n".join(lines).rstrip() + "\n"


def write_review_checklist(content: str, output_path: str | Path, *, overwrite: bool = False) -> Path:
    """把 review checklist 写到固定路径。"""

    path = Path(output_path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
