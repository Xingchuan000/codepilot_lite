from __future__ import annotations

"""第十五步 Post-PR automation 的人工审批请求与审批决策。"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, Template

from codepilot.post_pr.models import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalDecisionStatus,
    ApprovalRequest,
    PostPRTerminalReason,
    to_post_pr_jsonable,
)
from codepilot.post_pr.state_store import atomic_write_json
from codepilot.repo.git_utils import sha256_file


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_approval_request(
    *,
    run_id: str,
    round_id: str,
    pr_feedback_manifest: dict[str, Any],
    auto_pr_manifest_path: str | Path,
    pr_feedback_manifest_path: str | Path,
    requested_actions: list[ApprovalAction],
    reason: str,
    expires_at: str | None = None,
) -> ApprovalRequest:
    pr = pr_feedback_manifest.get("pr") or {}
    freshness = pr_feedback_manifest.get("feedback_freshness") or {}
    head_sha = freshness.get("current_head_sha") or pr.get("head_sha")
    return ApprovalRequest(
        run_id=run_id,
        round_id=round_id,
        requested_actions=requested_actions,
        reason=reason,
        pr_url=pr.get("url"),
        head_branch=pr.get("head_branch"),
        head_sha=head_sha,
        auto_pr_manifest_sha256=sha256_file(auto_pr_manifest_path),
        pr_feedback_manifest_sha256=sha256_file(pr_feedback_manifest_path),
        approval_request_sha256=None,
        expires_at=expires_at,
        feedback_manifest_path=Path(pr_feedback_manifest_path),
        followup_task_path=Path(pr_feedback_manifest_path).with_name("followup_task.md"),
        pr_update_plan_path=Path(pr_feedback_manifest_path).with_name("pr_update_plan.md"),
        created_at=_now_iso(),
    )


def approval_request_scope_payload(request: ApprovalRequest) -> dict[str, Any]:
    payload = to_post_pr_jsonable(request)
    if isinstance(payload, dict):
        payload.pop("approval_request_sha256", None)
    return payload


def compute_approval_request_scope_sha256(request: ApprovalRequest) -> str:
    payload = approval_request_scope_payload(request)
    return __import__("hashlib").sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_approval_request_integrity(request: ApprovalRequest) -> list[str]:
    expected = compute_approval_request_scope_sha256(request)
    if request.approval_request_sha256 != expected:
        return ["stale_approval: approval_request scope hash mismatch"]
    return []


_APPROVAL_TEMPLATE = Template(
    """# CodePilot Post-PR Approval Request

## Round

- Run ID: {{ request.run_id }}
- Round ID: {{ request.round_id }}
- Requested Actions: {{ request.requested_actions | join(", ") }}
- Reason: {{ request.reason }}
- PR URL: {{ request.pr_url or "n/a" }}
- Head Branch: {{ request.head_branch or "n/a" }}
- Head SHA: {{ request.head_sha or "n/a" }}

## Safety Gate

- Auto PR manifest SHA256: {{ request.auto_pr_manifest_sha256 or "n/a" }}
- PR feedback manifest SHA256: {{ request.pr_feedback_manifest_sha256 or "n/a" }}
- Approval request SHA256: {{ request.approval_request_sha256 or "n/a" }}
- Expires At: {{ request.expires_at or "n/a" }}

## Approval Scope

- This request only covers the explicit actions listed above.
- It does not include full logs, full diffs, prompts, or environment values.
- The approval must match the run ID, round ID, head SHA, and manifest hashes.

## Artifacts to Review

{% for item in artifacts %}
- {{ item }}
{% endfor %}

## Approval Decision Template

```json
{
  "schema_version": "codepilot.post_pr.approval_decision.v1",
  "run_id": "{{ request.run_id }}",
  "round_id": "{{ request.round_id }}",
  "status": "approved",
  "approved_actions": {{ approved_actions_json }},
  "reason": "Reviewed artifacts and approve selected actions only.",
  "head_sha": "{{ request.head_sha or '' }}",
  "auto_pr_manifest_sha256": "{{ request.auto_pr_manifest_sha256 or '' }}",
  "pr_feedback_manifest_sha256": "{{ request.pr_feedback_manifest_sha256 or '' }}",
  "approval_request_sha256": "{{ request.approval_request_sha256 or '' }}",
  "expires_at": "{{ request.expires_at or '' }}"
}
```
"""
)


def render_approval_request_markdown(
    request: ApprovalRequest,
    *,
    feedback_summary: dict[str, Any] | None = None,
) -> str:
    artifacts = [
        str(request.feedback_manifest_path) if request.feedback_manifest_path else "n/a",
        str(request.followup_task_path) if request.followup_task_path else "n/a",
        str(request.pr_update_plan_path) if request.pr_update_plan_path else "n/a",
    ]
    if feedback_summary:
        artifacts.append("feedback summary available")
    return _APPROVAL_TEMPLATE.render(
        request=request,
        artifacts=artifacts,
        approved_actions_json=json.dumps(request.requested_actions, ensure_ascii=False),
    )


def write_approval_request(
    request: ApprovalRequest,
    *,
    output_md: str | Path,
    output_json: str | Path,
    overwrite: bool = False,
) -> tuple[Path, Path, ApprovalRequest]:
    request_json_path = Path(output_json)
    md_path = Path(output_md)
    if not overwrite and (request_json_path.exists() or md_path.exists()):
        raise FileExistsError(request_json_path if request_json_path.exists() else md_path)
    request_sha = compute_approval_request_scope_sha256(request)
    updated_request = replace(request, approval_request_sha256=request_sha)
    atomic_write_json(to_post_pr_jsonable(updated_request), request_json_path, overwrite=overwrite)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_approval_request_markdown(updated_request), encoding="utf-8")
    return md_path, request_json_path, updated_request


def load_approval_request(path: str | Path) -> ApprovalRequest:
    request_path = Path(path)
    if not request_path.exists():
        raise FileNotFoundError(request_path)
    try:
        data = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid post_pr approval_request JSON: {request_path}") from exc
    if not isinstance(data, dict):
        raise ValueError("approval_request must be a JSON object")
    approved_actions = data.get("requested_actions") or []
    if not isinstance(approved_actions, list) or any(not isinstance(item, str) for item in approved_actions):
        raise ValueError("requested_actions must be a list[str]")
    return ApprovalRequest(
        run_id=str(data.get("run_id") or ""),
        round_id=str(data.get("round_id") or ""),
        requested_actions=[str(item) for item in approved_actions],
        reason=str(data.get("reason") or ""),
        pr_url=data.get("pr_url"),
        head_branch=data.get("head_branch"),
        head_sha=data.get("head_sha"),
        auto_pr_manifest_sha256=data.get("auto_pr_manifest_sha256"),
        pr_feedback_manifest_sha256=data.get("pr_feedback_manifest_sha256"),
        approval_request_sha256=data.get("approval_request_sha256"),
        expires_at=data.get("expires_at"),
        feedback_manifest_path=Path(data["feedback_manifest_path"]) if data.get("feedback_manifest_path") else None,
        followup_task_path=Path(data["followup_task_path"]) if data.get("followup_task_path") else None,
        pr_update_plan_path=Path(data["pr_update_plan_path"]) if data.get("pr_update_plan_path") else None,
        created_at=data.get("created_at"),
    )


def load_approval_decision(path: str | Path) -> ApprovalDecision:
    decision_path = Path(path)
    if not decision_path.exists():
        raise FileNotFoundError(decision_path)
    try:
        data = json.loads(decision_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid post_pr approval_decision JSON: {decision_path}") from exc
    if not isinstance(data, dict):
        raise ValueError("approval_decision must be a JSON object")
    approved_actions = data.get("approved_actions") or []
    if not isinstance(approved_actions, list) or any(not isinstance(item, str) for item in approved_actions):
        raise ValueError("approved_actions must be a list[str]")
    status = data.get("status")
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("invalid approval_decision status")
    return ApprovalDecision(
        run_id=str(data.get("run_id") or ""),
        round_id=str(data.get("round_id") or ""),
        status=status,  # type: ignore[arg-type]
        approved_actions=[str(item) for item in approved_actions],
        reason=data.get("reason"),
        decided_at=data.get("decided_at"),
        approver=data.get("approver"),
        head_sha=data.get("head_sha"),
        auto_pr_manifest_sha256=data.get("auto_pr_manifest_sha256"),
        pr_feedback_manifest_sha256=data.get("pr_feedback_manifest_sha256"),
        approval_request_sha256=data.get("approval_request_sha256"),
        expires_at=data.get("expires_at"),
    )


def synthesize_cli_approval_decision(
    *,
    request: ApprovalRequest,
    approve_run_agent: bool,
    approve_push_update: bool,
    approve_comment: bool,
    reason: str = "Approved by explicit CLI flags.",
) -> ApprovalDecision | None:
    if not approve_run_agent and not approve_push_update and not approve_comment:
        return None
    approved_actions: list[ApprovalAction] = []
    for action, allowed in [
        ("run_agent", approve_run_agent),
        ("push_update", approve_push_update),
        ("post_comment", approve_comment),
    ]:
        if allowed and action in request.requested_actions:
            approved_actions.append(action)
    return ApprovalDecision(
        run_id=request.run_id,
        round_id=request.round_id,
        status="approved",
        approved_actions=approved_actions,
        reason=reason,
        decided_at=_now_iso(),
        approver="cli",
        head_sha=request.head_sha,
        auto_pr_manifest_sha256=request.auto_pr_manifest_sha256,
        pr_feedback_manifest_sha256=request.pr_feedback_manifest_sha256,
        approval_request_sha256=request.approval_request_sha256,
        expires_at=request.expires_at,
    )


def validate_approval_decision(
    decision: ApprovalDecision,
    *,
    request: ApprovalRequest,
    existing_commit_sha: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    current = now or datetime.now(UTC)
    if decision.run_id != request.run_id:
        errors.append("run_id mismatch")
    if decision.round_id != request.round_id:
        errors.append("round_id mismatch")
    if decision.status not in {"pending", "approved", "rejected"}:
        errors.append("invalid approval decision status")
    for action in decision.approved_actions:
        if action not in {"run_agent", "push_update", "post_comment"}:
            errors.append(f"invalid approved action: {action}")
        elif action not in request.requested_actions:
            errors.append(f"approved action not requested: {action}")
    if decision.status == "approved" and not decision.head_sha:
        errors.append("missing head_sha")
    if decision.status == "approved" and not decision.auto_pr_manifest_sha256:
        errors.append("missing auto_pr_manifest_sha256")
    if decision.status == "approved" and not decision.pr_feedback_manifest_sha256:
        errors.append("missing pr_feedback_manifest_sha256")
    if decision.status == "approved" and not decision.approval_request_sha256:
        errors.append("missing approval_request_sha256")
    if decision.status == "approved" and request.head_sha and decision.head_sha != request.head_sha:
        errors.append("stale_approval: head_sha mismatch")
    if (
        decision.status == "approved"
        and request.auto_pr_manifest_sha256
        and decision.auto_pr_manifest_sha256 != request.auto_pr_manifest_sha256
    ):
        errors.append("stale_approval: auto_pr_manifest_sha256 mismatch")
    if (
        decision.status == "approved"
        and request.pr_feedback_manifest_sha256
        and decision.pr_feedback_manifest_sha256 != request.pr_feedback_manifest_sha256
    ):
        errors.append("stale_approval: pr_feedback_manifest_sha256 mismatch")
    if (
        decision.status == "approved"
        and request.approval_request_sha256
        and decision.approval_request_sha256 != request.approval_request_sha256
    ):
        errors.append("stale_approval: approval_request_sha256 mismatch")
    expires_at = _parse_iso_datetime(decision.expires_at)
    if expires_at is not None and expires_at < current:
        errors.append("approval_expired")
    if decision.status == "approved" and "push_update" in decision.approved_actions and "run_agent" not in decision.approved_actions and existing_commit_sha is None:
        errors.append("push_update requires an existing commit or run_agent approval")
    if "post_comment" in decision.approved_actions and decision.status != "approved":
        errors.append("post_comment requires approved decision")
    return errors


def approval_terminal_reason(errors: list[str]) -> PostPRTerminalReason:
    if any("expired" in error for error in errors):
        return "approval_expired"
    if any("stale_approval" in error or "mismatch" in error or "hash" in error for error in errors):
        return "stale_approval"
    return "approval_rejected"


def is_action_approved(decision: ApprovalDecision | None, action: ApprovalAction) -> bool:
    return decision is not None and decision.status == "approved" and action in decision.approved_actions


def write_approval_decision(decision: ApprovalDecision, output_path: str | Path, *, overwrite: bool = True) -> Path:
    return atomic_write_json(to_post_pr_jsonable(decision), output_path, overwrite=overwrite)
