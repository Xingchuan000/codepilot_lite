from __future__ import annotations

"""第十五步 Post-PR automation 的 state / side effects 持久化。"""

import json
import os
import shutil
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.pr_assist.manifest_loader import scan_token_like_strings
from codepilot.post_pr.models import (
    PostPRRoundRef,
    PostPRStateLockedError,
    SideEffectEntry,
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def resolve_post_pr_dir(run_dir: str | Path) -> Path:
    return Path(run_dir).expanduser().resolve() / "post_pr"


def resolve_state_path(run_dir: str | Path) -> Path:
    return resolve_post_pr_dir(run_dir) / "state.json"


def resolve_side_effects_path(run_dir: str | Path) -> Path:
    return resolve_post_pr_dir(run_dir) / "side_effects.json"


def atomic_write_json(payload: dict[str, Any], path: str | Path, *, overwrite: bool = True) -> Path:
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


def initial_post_pr_state(*, run_id: str, run_dir: Path, max_rounds: int) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": "codepilot.post_pr.state.v1",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "created_at": now,
        "updated_at": now,
        "max_rounds": max_rounds,
        "status": "planned",
        "terminal_reason": "none",
        "rounds": [],
        "latest_round_id": None,
        "blockers": [],
        "warnings": [],
    }


def load_post_pr_state(path: str | Path) -> dict[str, Any] | None:
    state_path = Path(path)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid post_pr state JSON: {state_path}") from exc
    if not isinstance(data, dict):
        raise ValueError("post_pr state must be a JSON object")
    return data


def write_post_pr_state(state: dict[str, Any], path: str | Path, *, overwrite: bool = True) -> Path:
    updated = dict(state)
    updated["updated_at"] = _now_iso()
    return atomic_write_json(updated, path, overwrite=overwrite)


def append_round_to_state(state: dict[str, Any], round_ref: PostPRRoundRef) -> dict[str, Any]:
    if state.get("terminal_reason") != "none" and state.get("status") in _TERMINAL_STATUSES:
        raise RuntimeError("state is already terminal")
    updated = dict(state)
    rounds = list(updated.get("rounds") or [])
    rounds.append(to_post_pr_jsonable(round_ref))
    updated["rounds"] = rounds
    updated["latest_round_id"] = round_ref.round_id
    updated["updated_at"] = _now_iso()
    return updated


def mark_terminal(state: dict[str, Any], *, status: str, terminal_reason: str, blockers: list[str] | None = None) -> dict[str, Any]:
    updated = dict(state)
    updated["status"] = status
    updated["terminal_reason"] = terminal_reason
    updated["blockers"] = list(blockers or [])
    updated["updated_at"] = _now_iso()
    return updated


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
        raise RuntimeError("state_locked") from exc
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


def initial_side_effects(run_id: str) -> dict[str, Any]:
    return {"schema_version": "codepilot.post_pr.side_effects.v1", "run_id": run_id, "effects": []}


def load_side_effects(path: str | Path, *, run_id: str) -> dict[str, Any]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return initial_side_effects(run_id)
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid post_pr side_effects JSON: {ledger_path}") from exc
    if not isinstance(ledger, dict):
        raise ValueError("post_pr side_effects must be a JSON object")
    if ledger.get("run_id") != run_id:
        raise ValueError("post_pr side_effects run_id mismatch")
    return ledger


def append_side_effect(ledger: dict[str, Any], effect: SideEffectEntry) -> dict[str, Any]:
    updated = dict(ledger)
    effects = list(updated.get("effects") or [])
    effects.append(to_post_pr_jsonable(effect))
    updated["effects"] = effects
    return updated


def has_succeeded_effect(
    ledger: dict[str, Any],
    *,
    round_id: str,
    action: str,
    approval_decision_sha256: str | None = None,
    commit_sha: str | None = None,
    comment_marker: str | None = None,
) -> bool:
    for effect in ledger.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        if effect.get("round_id") != round_id or effect.get("action") != action or effect.get("status") != "succeeded":
            continue
        if approval_decision_sha256 is not None and effect.get("approval_decision_sha256") != approval_decision_sha256:
            continue
        if commit_sha is not None and effect.get("commit_sha") != commit_sha:
            continue
        if comment_marker is not None and effect.get("comment_marker") != comment_marker:
            continue
        return True
    return False


def latest_successful_commit_for_round(ledger: dict[str, Any], *, round_id: str) -> str | None:
    for effect in reversed(ledger.get("effects") or []):
        if not isinstance(effect, dict):
            continue
        if effect.get("round_id") != round_id or effect.get("status") != "succeeded":
            continue
        if effect.get("commit_sha"):
            return str(effect["commit_sha"])
    return None

