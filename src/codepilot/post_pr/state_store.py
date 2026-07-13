from __future__ import annotations

"""第十五步 Post-PR automation 的 state / side effects 持久化。"""

import json
import os
import shutil
import socket
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.pr_assist.manifest_loader import scan_token_like_strings
from codepilot.post_pr.models import (
    PostPRAutomationState,
    PostPRAutomationStatus,
    PostPRRoundRef,
    PostPRStateLockedError,
    PostPRTerminalReason,
    SideEffectEntry,
    SideEffectLedger,
    to_post_pr_jsonable,
)


POST_PR_ARTIFACT_NAMES = [
    "state.json",
    "side_effects.json",
    "approval_request.md",
    "approval_request.json",
    "approval_decision.json",
    "post_pr_automation_manifest.json",
    "post_pr_automation_report.md",
    "post_pr_automation_workflow.yml",
]

_TERMINAL_STATUSES = {"blocked", "failed", "no_feedback", "max_rounds_reached", "repeated_feedback"}
_STATE_STATUSES = {
    "planned",
    "awaiting_approval",
    "feedback_found",
    "agent_ran",
    "patch_ready",
    "branch_updated",
    "no_feedback",
    "max_rounds_reached",
    "repeated_feedback",
    "blocked",
    "failed",
}
_TERMINAL_REASONS = {
    "none",
    "no_feedback",
    "awaiting_approval",
    "approval_rejected",
    "approval_expired",
    "stale_approval",
    "max_rounds_reached",
    "repeated_feedback",
    "stale_head",
    "pending_checks",
    "ci_timeout",
    "checks_cancelled",
    "checks_skipped",
    "checks_unavailable",
    "unsafe_branch",
    "agent_failed",
    "push_failed",
    "comment_failed",
    "api_degraded",
    "manifest_invalid",
    "state_locked",
}
_SIDE_EFFECT_ACTIONS = {"run_agent", "commit", "push_update", "post_comment"}
_SIDE_EFFECT_STATUSES = {"planned", "succeeded", "failed", "skipped"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def resolve_post_pr_dir(run_dir: str | Path) -> Path:
    return Path(run_dir).expanduser().resolve() / "post_pr"


def resolve_state_path(run_dir: str | Path) -> Path:
    return resolve_post_pr_dir(run_dir) / "state.json"


def resolve_side_effects_path(run_dir: str | Path) -> Path:
    return resolve_post_pr_dir(run_dir) / "side_effects.json"


def atomic_write_json(payload: object, path: str | Path, *, overwrite: bool = True) -> Path:
    """原子写 JSON，先做敏感信息检查，再落盘到临时文件后 replace。"""

    jsonable = to_post_pr_jsonable(payload)
    if scan_token_like_strings(jsonable):
        raise ValueError("token-like string detected in post_pr payload")
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    if json_path.exists() and not overwrite:
        raise FileExistsError(json_path)
    tmp_path = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(jsonable, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(json_path)
    return json_path


def initial_post_pr_state(*, run_id: str, run_dir: Path, max_rounds: int) -> PostPRAutomationState:
    now = _now_iso()
    return PostPRAutomationState(
        schema_version="codepilot.post_pr.state.v1",
        run_id=run_id,
        run_dir=run_dir,
        created_at=now,
        updated_at=now,
        max_rounds=max_rounds,
    )


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    """读取 v1 中必须为数组的字段，不在持久化边界外猜测错误类型。"""

    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"post_pr state field must be a list: {key}")
    return value


def _required_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    """要求持久化字段为字符串，避免损坏 JSON 泄漏 KeyError 或 TypeError。"""

    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{context} field must be a string: {key}")
    return value


def _optional_string(payload: dict[str, Any], key: str, *, context: str) -> str | None:
    if key not in payload:
        raise ValueError(f"{context} missing field: {key}")
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{context} field must be a string or null: {key}")
    return value


def _round_ref_from_json(item: dict[str, Any]) -> PostPRRoundRef:
    """把 state.json 中的一轮恢复成领域对象；路径只在这里转回 Path。"""

    round_index = item.get("round_index")
    if not isinstance(round_index, int) or isinstance(round_index, bool):
        raise ValueError("post_pr round field must be an integer: round_index")
    fingerprints = item.get("feedback_fingerprints")
    blockers = item.get("blockers")
    warnings = item.get("warnings")
    if not isinstance(fingerprints, list) or not all(isinstance(value, str) for value in fingerprints):
        raise ValueError("post_pr round feedback_fingerprints must contain strings")
    if not isinstance(blockers, list) or not all(isinstance(value, str) for value in blockers):
        raise ValueError("post_pr round blockers must contain strings")
    if not isinstance(warnings, list) or not all(isinstance(value, str) for value in warnings):
        raise ValueError("post_pr round warnings must contain strings")
    for key in ("agent_ran", "patch_generated", "commit_created", "push_update_executed", "comment_posted"):
        if not isinstance(item.get(key), bool):
            raise ValueError(f"post_pr round field must be a boolean: {key}")
    return PostPRRoundRef(
        round_id=_required_string(item, "round_id", context="post_pr round"),
        round_index=round_index,
        round_dir=Path(_required_string(item, "round_dir", context="post_pr round")),
        collect_manifest_path=Path(value)
        if (value := _optional_string(item, "collect_manifest_path", context="post_pr round"))
        else None,
        execute_manifest_path=Path(value)
        if (value := _optional_string(item, "execute_manifest_path", context="post_pr round"))
        else None,
        latest_pr_feedback_manifest_path=Path(value)
        if (value := _optional_string(item, "latest_pr_feedback_manifest_path", context="post_pr round"))
        else None,
        feedback_fingerprints=fingerprints,
        status=_required_string(item, "status", context="post_pr round"),
        terminal_reason=_required_string(item, "terminal_reason", context="post_pr round"),
        head_sha_before=_optional_string(item, "head_sha_before", context="post_pr round"),
        head_sha_after=_optional_string(item, "head_sha_after", context="post_pr round"),
        agent_ran=item["agent_ran"],
        patch_generated=item["patch_generated"],
        commit_created=item["commit_created"],
        new_commit_sha=_optional_string(item, "new_commit_sha", context="post_pr round"),
        push_update_executed=item["push_update_executed"],
        comment_posted=item["comment_posted"],
        blockers=blockers,
        warnings=warnings,
    )


def _state_from_json(payload: dict[str, Any]) -> PostPRAutomationState:
    """集中校验并解析 state v1，Controller 只接收规范化状态。"""

    if payload.get("schema_version") != "codepilot.post_pr.state.v1":
        raise ValueError("post_pr state schema_version mismatch")
    required_fields = {
        "run_id",
        "run_dir",
        "created_at",
        "updated_at",
        "max_rounds",
        "status",
        "terminal_reason",
        "rounds",
        "latest_round_id",
        "blockers",
        "warnings",
    }
    missing_fields = required_fields - payload.keys()
    if missing_fields:
        raise ValueError(f"post_pr state missing fields: {', '.join(sorted(missing_fields))}")
    max_rounds = payload["max_rounds"]
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool):
        raise ValueError("post_pr state field must be an integer: max_rounds")
    if max_rounds < 1 or max_rounds > 3:
        raise ValueError("post_pr state max_rounds must be between 1 and 3")
    status = _required_string(payload, "status", context="post_pr state")
    terminal_reason = _required_string(payload, "terminal_reason", context="post_pr state")
    if status not in _STATE_STATUSES:
        raise ValueError("post_pr state status is invalid")
    if terminal_reason not in _TERMINAL_REASONS:
        raise ValueError("post_pr state terminal_reason is invalid")
    blockers = _required_list(payload, "blockers")
    warnings = _required_list(payload, "warnings")
    if not all(isinstance(value, str) for value in blockers):
        raise ValueError("post_pr state blockers must contain strings")
    if not all(isinstance(value, str) for value in warnings):
        raise ValueError("post_pr state warnings must contain strings")
    rounds = _required_list(payload, "rounds")
    if not all(isinstance(item, dict) for item in rounds):
        raise ValueError("post_pr state rounds must contain JSON objects")
    return PostPRAutomationState(
        schema_version=str(payload["schema_version"]),
        run_id=_required_string(payload, "run_id", context="post_pr state"),
        run_dir=Path(_required_string(payload, "run_dir", context="post_pr state")),
        created_at=_required_string(payload, "created_at", context="post_pr state"),
        updated_at=_required_string(payload, "updated_at", context="post_pr state"),
        max_rounds=max_rounds,
        status=status,
        terminal_reason=terminal_reason,
        rounds=tuple(_round_ref_from_json(item) for item in rounds),
        latest_round_id=_optional_string(payload, "latest_round_id", context="post_pr state"),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def load_post_pr_state(path: str | Path) -> PostPRAutomationState | None:
    state_path = Path(path)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid post_pr state JSON: {state_path}") from exc
    if not isinstance(data, dict):
        raise ValueError("post_pr state must be a JSON object")
    return _state_from_json(data)


def write_post_pr_state(state: PostPRAutomationState, path: str | Path, *, overwrite: bool = True) -> Path:
    return atomic_write_json(replace(state, updated_at=_now_iso()), path, overwrite=overwrite)


def upsert_round(state: PostPRAutomationState, round_ref: PostPRRoundRef) -> PostPRAutomationState:
    """按 round_id 替换或追加轮次，恢复同一轮时不会生成重复记录。

    过滤后追加会保留其他轮次原有执行顺序；被更新轮次仍位于最新位置，
    latest_round_id 与实际最新写入的轮次保持一致。
    """

    if state.terminal_reason != "none" and state.status in _TERMINAL_STATUSES:
        raise RuntimeError("state is already terminal")
    rounds = tuple(existing for existing in state.rounds if existing.round_id != round_ref.round_id)
    return replace(state, rounds=rounds + (round_ref,), latest_round_id=round_ref.round_id, updated_at=_now_iso())


def mark_terminal(
    state: PostPRAutomationState,
    *,
    status: PostPRAutomationStatus,
    terminal_reason: PostPRTerminalReason,
    blockers: list[str] | None = None,
) -> PostPRAutomationState:
    """以不可变替换方式写入当前终止或等待状态。"""

    return replace(
        state,
        status=status,
        terminal_reason=terminal_reason,
        blockers=tuple(blockers or []),
        updated_at=_now_iso(),
    )


def clear_post_pr_dir(post_pr_dir: str | Path) -> None:
    """只清理 post_pr 自己的产物，不碰 run_dir 根目录的上游文件。"""

    root = Path(post_pr_dir)
    if not root.exists():
        return
    for name in POST_PR_ARTIFACT_NAMES:
        target = root / name
        if target.is_file() or target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    for child in root.iterdir():
        if child.name.startswith("round-") and child.is_dir():
            shutil.rmtree(child)


@dataclass(frozen=True)
class StateLock:
    path: Path
    acquired: bool = False


def acquire_state_lock(post_pr_dir: str | Path) -> StateLock:
    lock_dir = Path(post_pr_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
    except FileExistsError as exc:
        raise PostPRStateLockedError("state_locked") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "created_at": _now_iso(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    return StateLock(path=lock_path, acquired=True)


def release_state_lock(lock: StateLock) -> None:
    if lock.acquired and lock.path.exists():
        lock.path.unlink()


def initial_side_effects(run_id: str) -> SideEffectLedger:
    return SideEffectLedger(schema_version="codepilot.post_pr.side_effects.v1", run_id=run_id)


def _side_effect_from_json(item: dict[str, Any]) -> SideEffectEntry:
    """把 ledger v1 的单条副作用记录恢复成不可变对象。"""

    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("post_pr side effect metadata must be a JSON object")
    action = _required_string(item, "action", context="post_pr side effect")
    status = _required_string(item, "status", context="post_pr side effect")
    if action not in _SIDE_EFFECT_ACTIONS:
        raise ValueError("post_pr side effect action is invalid")
    if status not in _SIDE_EFFECT_STATUSES:
        raise ValueError("post_pr side effect status is invalid")
    return SideEffectEntry(
        round_id=_required_string(item, "round_id", context="post_pr side effect"),
        action=action,
        status=status,
        approval_decision_sha256=_optional_string(item, "approval_decision_sha256", context="post_pr side effect"),
        head_sha_before=_optional_string(item, "head_sha_before", context="post_pr side effect"),
        head_sha_after=_optional_string(item, "head_sha_after", context="post_pr side effect"),
        commit_sha=_optional_string(item, "commit_sha", context="post_pr side effect"),
        remote_ref=_optional_string(item, "remote_ref", context="post_pr side effect"),
        comment_marker=_optional_string(item, "comment_marker", context="post_pr side effect"),
        executed_at=_optional_string(item, "executed_at", context="post_pr side effect"),
        error=_optional_string(item, "error", context="post_pr side effect"),
        metadata=metadata,
    )


def _ledger_from_json(payload: dict[str, Any]) -> SideEffectLedger:
    """集中校验并解析 side_effects v1。"""

    if payload.get("schema_version") != "codepilot.post_pr.side_effects.v1":
        raise ValueError("post_pr side_effects schema_version mismatch")
    if "run_id" not in payload or "effects" not in payload:
        raise ValueError("post_pr side_effects missing required fields")
    effects = payload.get("effects")
    if not isinstance(effects, list) or not all(isinstance(item, dict) for item in effects):
        raise ValueError("post_pr side_effects effects must contain JSON objects")
    return SideEffectLedger(
        schema_version=str(payload["schema_version"]),
        run_id=_required_string(payload, "run_id", context="post_pr side_effects"),
        effects=tuple(_side_effect_from_json(item) for item in effects),
    )


def load_side_effects(path: str | Path, *, run_id: str) -> SideEffectLedger:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return initial_side_effects(run_id)
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid post_pr side_effects JSON: {ledger_path}") from exc
    if not isinstance(ledger, dict):
        raise ValueError("post_pr side_effects must be a JSON object")
    parsed = _ledger_from_json(ledger)
    if parsed.run_id != run_id:
        raise ValueError("post_pr side_effects run_id mismatch")
    return parsed


def append_side_effect(ledger: SideEffectLedger, effect: SideEffectEntry) -> SideEffectLedger:
    return replace(ledger, effects=ledger.effects + (effect,))


def has_succeeded_effect(
    ledger: SideEffectLedger,
    *,
    round_id: str,
    action: str,
    approval_decision_sha256: str | None = None,
    commit_sha: str | None = None,
    comment_marker: str | None = None,
) -> bool:
    for effect in ledger.effects:
        if effect.round_id != round_id or effect.action != action or effect.status != "succeeded":
            continue
        if approval_decision_sha256 is not None and effect.approval_decision_sha256 != approval_decision_sha256:
            continue
        if commit_sha is not None and effect.commit_sha != commit_sha:
            continue
        if comment_marker is not None and effect.comment_marker != comment_marker:
            continue
        return True
    return False


def latest_successful_commit_for_round(ledger: SideEffectLedger, *, round_id: str) -> str | None:
    for effect in reversed(ledger.effects):
        if effect.round_id != round_id or effect.status != "succeeded":
            continue
        if effect.commit_sha:
            return effect.commit_sha
    return None
