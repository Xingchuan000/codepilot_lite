from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from typing import Any

from codepilot.tui_agent.models import PermissionMode, ProjectContext, TUISession, TUISessionRunRef, to_jsonable

SESSION_SCHEMA_VERSION = "codepilot.tui_agent.session.v1"


def make_session_id() -> str:
    return f"session-{uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def task_preview(text: str, max_chars: int = 120) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _absolute_path(value: str | None, base: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _session_dir(project: ProjectContext, session_id: str) -> Path:
    return project.workspace_root / ".codepilot" / "sessions" / session_id


class SessionStore:
    def __init__(self, project: ProjectContext) -> None:
        self.project = project

    def _session_paths(self, session_id: str) -> tuple[Path, Path, Path, Path]:
        session_dir = _session_dir(self.project, session_id)
        return session_dir, session_dir / "session.json", session_dir / "messages.jsonl", session_dir / "runs.jsonl"

    def create_session(
        self,
        *,
        model: str | None,
        permission_mode: PermissionMode,
        metadata: dict[str, Any] | None = None,
    ) -> TUISession:
        session_id = make_session_id()
        session_dir, session_path, messages_path, runs_index_path = self._session_paths(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        messages_path.write_text("", encoding="utf-8")
        runs_index_path.write_text("", encoding="utf-8")
        created_at = now_iso()
        session = TUISession(
            schema_version=SESSION_SCHEMA_VERSION,
            session_id=session_id,
            project_path=self.project.resolved_project,
            git_root=self.project.git_root,
            workspace_root=self.project.workspace_root,
            created_at=created_at,
            updated_at=created_at,
            title=self.project.resolved_project.name or "project",
            model=model,
            permission_mode=permission_mode,
            runs_dir=self.project.default_runs_dir,
            session_dir=session_dir,
            messages_path=messages_path,
            runs_index_path=runs_index_path,
            metadata={
                "git_dirty_status": self.project.git_dirty_status,
                "mcp_config": str(self.project.mcp_config_path) if self.project.mcp_config_path else None,
                **(metadata or {}),
            },
        )
        self._write_session(session)
        return session

    def append_message(self, session: TUISession, *, role: str, content: str, run_id: str | None = None) -> None:
        _append_jsonl(
            session.messages_path,
            {"timestamp": now_iso(), "role": role, "content": content, "run_id": run_id},
        )

    def append_run(self, session: TUISession, run_ref: TUISessionRunRef) -> TUISession:
        _append_jsonl(session.runs_index_path, {"timestamp": now_iso(), **to_jsonable(run_ref)})
        updated = replace(session, runs=session.runs + (run_ref,), last_run_id=run_ref.run_id, updated_at=now_iso())
        self._write_session(updated)
        return updated

    def update_session(self, session: TUISession, **changes: Any) -> TUISession:
        updated = replace(session, updated_at=now_iso(), **changes)
        self._write_session(updated)
        return updated

    def load_session(self, session_id: str) -> TUISession:
        _, session_path, messages_path, runs_index_path = self._session_paths(session_id)
        payload = json.loads(session_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SESSION_SCHEMA_VERSION:
            raise ValueError("session.json schema_version mismatch")
        runs = tuple(
            TUISessionRunRef(
                run_id=item["run_id"],
                task_preview=item["task_preview"],
                status=item["status"],
                trace_path=item.get("trace_path"),
                report_path=item.get("report_path"),
                report_json_path=item.get("report_json_path"),
                started_at=item.get("started_at"),
                ended_at=item.get("ended_at"),
                changed_files=tuple(item.get("changed_files", [])),
                tests=item.get("tests"),
            )
            for item in payload.get("runs", [])
        )
        workspace_root = Path(payload["workspace_root"]).expanduser().resolve()
        session_dir_value = Path(payload["session_dir"])
        session_dir = session_dir_value if session_dir_value.is_absolute() else (workspace_root / session_dir_value).resolve()
        return TUISession(
            schema_version=payload["schema_version"],
            session_id=payload["session_id"],
            project_path=_absolute_path(payload["project_path"], workspace_root) or workspace_root,
            git_root=_absolute_path(payload.get("git_root"), workspace_root),
            workspace_root=workspace_root,
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            title=payload["title"],
            model=payload.get("model"),
            permission_mode=payload["permission_mode"],
            runs_dir=_absolute_path(payload["runs_dir"], workspace_root) or workspace_root / "runs",
            session_dir=session_dir,
            messages_path=messages_path,
            runs_index_path=runs_index_path,
            runs=runs,
            last_run_id=payload.get("last_run_id"),
            metadata=payload.get("metadata", {}),
        )

    def _write_session(self, session: TUISession) -> None:
        session_path = session.session_dir / "session.json"
        payload = {
            "schema_version": session.schema_version,
            "session_id": session.session_id,
            "project_path": _relative_path(session.project_path, session.workspace_root),
            "git_root": _relative_path(session.git_root, session.workspace_root) if session.git_root else None,
            "workspace_root": str(session.workspace_root),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "title": session.title,
            "model": session.model,
            "permission_mode": session.permission_mode,
            "runs_dir": _relative_path(session.runs_dir, session.workspace_root),
            "session_dir": _relative_path(session.session_dir, session.workspace_root),
            "messages_path": _relative_path(session.messages_path, session.workspace_root),
            "runs_index_path": _relative_path(session.runs_index_path, session.workspace_root),
            "runs": [to_jsonable(run_ref) for run_ref in session.runs],
            "last_run_id": session.last_run_id,
            "metadata": to_jsonable(session.metadata),
        }
        _atomic_write_json(session_path, payload)
