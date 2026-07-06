from __future__ import annotations

"""第十二步 Manual PR Assist 使用的数据模型。"""

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal


SafetyGateStatus = Literal["pass", "warn", "fail"]
PRAssistSideEffectKey = Literal[
    "branch_prepared",
    "commit_prepared",
    "push_executed",
    "pr_created",
    "github_api_called",
]
PRAssistStatus = Literal[
    "generated",
    "blocked_by_safety",
    "manifest_invalid",
    "patch_invalid",
    "branch_prepared",
    "branch_failed",
    "commit_prepared",
    "commit_failed",
]


@dataclass(frozen=True)
class PRAssistInput:
    """描述一次 pr-assist 运行的最小输入。"""

    run_id: str
    run_dir: Path
    manifest_path: Path
    redact_absolute_paths: bool = True
    strict_safety: bool = True


@dataclass(frozen=True)
class PRAssistArtifact:
    """描述 pr-assist 生成出的单个产物。"""

    name: str
    path: Path
    kind: str
    exists: bool
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class PRAssistSafetyGate:
    """把第十一步 manifest 压缩成 pr-assist 需要的安全门结论。"""

    status: SafetyGateStatus
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PRBodyData:
    """渲染 pr_body.md 需要的结构化输入。"""

    title: str
    issue_ref: str | None
    summary: str
    changed_files: list[str]
    tests: list[str]
    safety_notes: list[str]
    report_path: str | None
    patch_path: str | None
    manifest_path: str | None
    restore_plan_path: str | None
    patch_sha256: str | None = None
    patch_empty: bool | None = None
    worktree_used: bool | None = None
    baseline_dirty: bool | None = None
    protected_changed_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ManualCommandPlan:
    """保存人工执行 PR 流程时需要展示的命令文本。"""

    commands: list[str]
    warnings: list[str] = field(default_factory=list)
    destructive_commands_included: bool = False
    push_commands_included: bool = False
    pr_create_commands_included: bool = False


@dataclass(frozen=True)
class PRAssistResult:
    """workflow 的最终返回对象。"""

    run_id: str
    run_dir: Path
    status: PRAssistStatus
    safety_gate: PRAssistSafetyGate
    pr_body_path: Path | None = None
    manual_commands_path: Path | None = None
    review_checklist_path: Path | None = None
    github_action_template_path: Path | None = None
    pr_assist_manifest_path: Path | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    warnings: list[str] = field(default_factory=list)


class PRAssistError(RuntimeError):
    """pr-assist 统一异常基类。"""


class ManifestInvalidError(PRAssistError):
    """artifact_manifest.json 结构或内容不符合预期。"""


class SafetyBlockedError(PRAssistError):
    """安全门阻止后续本地副作用操作。"""


class BranchPrepError(PRAssistError):
    """本地分支准备失败。"""


class CommitPrepError(PRAssistError):
    """本地提交准备失败。"""


def to_pr_assist_jsonable(value: Any) -> Any:
    """把 Path / dataclass 递归转换成可直接写 JSON 的结构。"""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_pr_assist_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_pr_assist_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_pr_assist_jsonable(item) for item in value]
    return value
