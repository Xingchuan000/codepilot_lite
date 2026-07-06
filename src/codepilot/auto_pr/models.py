from __future__ import annotations

"""第十三步 Controlled Auto PR 的核心数据模型。"""

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal


AutoPRStatus = Literal[
    "planned",
    "planned_with_blockers",
    "blocked_by_safety",
    "manifest_invalid",
    "push_prepared",
    "pushed",
    "pr_created",
    "comment_posted",
    "failed",
]

RemoteActionMode = Literal["dry_run", "execute"]
AutoPRSafetyStatus = Literal["pass", "warn", "fail"]


class AutoPRError(RuntimeError):
    """Controlled Auto PR 统一异常基类。"""


class AutoPRManifestInvalidError(AutoPRError):
    """pr_assist_manifest 或相关依赖产物不合法。"""


class AutoPRSafetyError(AutoPRError):
    """安全门不允许继续执行远端副作用。"""


class AutoPRGitError(AutoPRError):
    """git / remote push 相关错误。"""


class AutoPRRemoteError(AutoPRError):
    """remote / repo slug / 分支名解析错误。"""


class AutoPRGitHubError(AutoPRError):
    """GitHub API 调用相关错误。"""


class AutoPRWorkflowInputError(AutoPRError):
    """CLI 或 workflow 输入参数不合法。"""


@dataclass(frozen=True)
class AutoPRInput:
    """描述一次 auto-pr workflow 运行所需的最小输入。"""

    run_id: str
    run_dir: Path
    pr_assist_manifest_path: Path
    dry_run: bool = True
    execute: bool = False
    allow_push: bool = False
    allow_create_pr: bool = False
    allow_comment: bool = False
    allow_empty_pr: bool = False
    remote_name: str = "origin"
    base_branch: str | None = None
    head_branch: str | None = None
    repo_slug: str | None = None
    token_env: str = "GITHUB_TOKEN"
    draft: bool = True
    generate_workflow_template: bool = True
    overwrite: bool = False


@dataclass(frozen=True)
class GitHubRepoRef:
    """标识 GitHub 上的目标仓库。"""

    owner: str
    repo: str
    remote_url: str | None = None


@dataclass(frozen=True)
class BranchPushPlan:
    """保存一次受控 push 的预计算计划，不直接触发副作用。"""

    remote_name: str
    local_branch: str
    remote_branch: str
    commit_sha: str
    base_branch: str
    push_refspec: str
    will_push: bool = False
    branch_collision: bool = False
    remote_ref_verified: bool = False
    remote_ref_sha: str | None = None


@dataclass(frozen=True)
class PRCreateRequest:
    """描述一次创建 PR 所需的最小请求参数。"""

    repo: GitHubRepoRef
    title: str
    body_path: Path
    head_branch: str
    base_branch: str
    draft: bool = True


@dataclass(frozen=True)
class PRCreateResult:
    """记录 PR 创建结果，同时避免把敏感信息落盘。"""

    created: bool
    number: int | None = None
    url: str | None = None
    api_called: bool = False
    request_id: str | None = None
    status_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class AutoPRSafetyGate:
    """把第十一步与第十二步的安全信息压缩成远端副作用判定。"""

    status: AutoPRSafetyStatus
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutoPRResult:
    """workflow 的统一返回对象。"""

    run_id: str
    run_dir: Path
    status: AutoPRStatus
    safety_gate: AutoPRSafetyGate
    auto_pr_plan_path: Path | None = None
    auto_pr_manifest_path: Path | None = None
    controlled_workflow_path: Path | None = None
    branch_push_plan: BranchPushPlan | None = None
    pr_request: PRCreateRequest | None = None
    pr_result: PRCreateResult | None = None
    push_executed: bool = False
    pr_created: bool = False
    github_api_called: bool = False
    comment_posted: bool = False
    warnings: list[str] = field(default_factory=list)


def to_auto_pr_jsonable(value: Any) -> Any:
    """把 Path / dataclass 递归转换成可安全写入 JSON 的结构。"""

    # 这里明确不接受把 token 作为字段落盘；如果未来出现类似字段，
    # 通过字段名先拦掉，避免敏感值进入 manifest。
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if "token" in str(key).lower():
                raise ValueError(f"token-like field is not allowed in auto_pr jsonable payload: {key}")
            result[key] = to_auto_pr_jsonable(item)
        return result
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: to_auto_pr_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [to_auto_pr_jsonable(item) for item in value]
    return value
