from __future__ import annotations

"""管理 follow-up agent run 的 attempt 目录。"""

import json
from pathlib import Path
from typing import Any

from codepilot.pr_feedback.models import FollowupAttemptRef


def next_followup_attempt_id(run_dir: str | Path) -> tuple[str, int]:
    """按 attempt-001 / attempt-002 的顺序寻找下一个可用目录名。"""

    followup_dir = Path(run_dir).expanduser().resolve() / "followup"
    existing = []
    if followup_dir.exists():
        for path in followup_dir.iterdir():
            if path.is_dir() and path.name.startswith("attempt-"):
                try:
                    existing.append(int(path.name.split("-", maxsplit=1)[1]))
                except ValueError:
                    continue
    attempt_index = max(existing, default=0) + 1
    return (f"attempt-{attempt_index:03d}", attempt_index)


def create_followup_attempt(
    run_dir: str | Path,
    *,
    source_feedback_manifest_path: str | Path,
    followup_task_path: str | Path,
    overwrite_attempt: bool = False,
) -> FollowupAttemptRef:
    """创建一个新的 follow-up attempt 目录，不触碰 run 根目录的 artifact。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    attempt_id, attempt_index = next_followup_attempt_id(run_dir_path)
    attempt_dir = run_dir_path / "followup" / attempt_id
    if attempt_dir.exists() and not overwrite_attempt:
        raise FileExistsError(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    return FollowupAttemptRef(
        attempt_id=attempt_id,
        attempt_index=attempt_index,
        parent_run_id=run_dir_path.name,
        attempt_dir=attempt_dir,
        source_feedback_manifest_path=Path(source_feedback_manifest_path).expanduser().resolve(),
        followup_task_path=Path(followup_task_path).expanduser().resolve(),
    )


def copy_followup_task_to_attempt(attempt: FollowupAttemptRef) -> Path:
    """把原 run 下的 follow-up task 复制到 attempt 目录。"""

    text = attempt.followup_task_path.read_text(encoding="utf-8")
    target = attempt.attempt_dir / "followup_task.md"
    target.write_text(text, encoding="utf-8")
    return target


def write_followup_attempt_manifest(
    attempt: FollowupAttemptRef,
    payload: dict[str, Any],
    *,
    overwrite: bool = True,
) -> Path:
    """写 attempt manifest，记录这次 follow-up 的最小上下文。"""

    path = attempt.attempt_dir / "followup_attempt_manifest.json"
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    body = {
        "schema_version": "codepilot.followup_attempt.v1",
        "attempt_id": attempt.attempt_id,
        "attempt_index": attempt.attempt_index,
        "parent_run_id": attempt.parent_run_id,
        "source_feedback_manifest_path": str(attempt.source_feedback_manifest_path),
        "followup_task_path": str(attempt.followup_task_path),
        **payload,
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
