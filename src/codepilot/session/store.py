from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from codepilot.permissions import PermissionRequest, permission_now_iso
from codepilot.session.database import SessionDatabase
from codepilot.session.ids import (
    make_attempt_id,
    make_artifact_id,
    make_event_id,
    make_message_id,
    make_part_id,
    make_project_id,
    make_session_id,
    make_tool_call_id,
    make_tool_result_id,
    make_turn_id,
    now_iso,
)
from codepilot.session.models import (
    ArtifactRecord,
    AttemptStatus,
    BranchConfirmationRequired,
    ContextSummaryRecord,
    MessagePartRecord,
    MessageRecord,
    PermissionGrantRecord,
    PermissionRequestRecord,
    PermissionResponseRecord,
    ProjectRecord,
    RunAttemptRecord,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    SessionSummary,
    ToolCallRecord,
    ToolCallStatus,
    ToolResultRecord,
    ToolResultStatus,
    TurnRecord,
    TurnSubmission,
    TurnStatus,
)
from codepilot.session.paths import SessionPaths, resolve_session_paths


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: str) -> Any:
    return json.loads(value)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _int_to_bool(value: Any) -> bool:
    return bool(int(value))


def _make_local_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _task_preview(text: str) -> str:
    """把首条用户消息压缩为稳定的 Session 标题。"""

    return " ".join(text.split())[:80] or "New session"


def _content_preview(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())[:120]
    return _json_dumps(content)[:120]


BLOCKING_TURN_STATUSES = {"queued", "running", "waiting_permission", "recovery_required"}


class SessionStore:
    """SQLite Session 的薄仓库层。

    这里不塞业务流程，只负责记录的增删改查和 JSON 编解码。
    """

    def __init__(self, database: SessionDatabase, paths: SessionPaths | None = None) -> None:
        self.database = database
        self.paths = paths or resolve_session_paths(database.path.parent)

    def create_project(self, path: Path) -> ProjectRecord:
        resolved = path.expanduser().resolve()
        created_at = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO projects(project_id, path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (make_project_id(), str(resolved), created_at, created_at),
                )
                row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
        return self._project_from_row(row)

    def get_or_create_project(self, path: Path) -> ProjectRecord:
        resolved = path.expanduser().resolve()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE path = ?", (str(resolved),)).fetchone()
            if row is not None:
                return self._project_from_row(row)
        return self.create_project(resolved)

    def create_session(
        self,
        *,
        project_path: Path,
        provider: str,
        current_model: str,
        permission_mode: str,
        title: str = "New session",
        initial_branch: str | None = None,
        current_branch: str | None = None,
        status: SessionStatus = "active",
        parent_session_id: str | None = None,
        forked_from_turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRecord:
        project = self.get_or_create_project(project_path)
        session_id = make_session_id()
        created_at = now_iso()
        payload = metadata or {}
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, project_id, title, provider, current_model, permission_mode,
                    initial_branch, current_branch, status, parent_session_id, forked_from_turn_id,
                    created_at, updated_at, last_activity_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    project.project_id,
                    title,
                    provider,
                    current_model,
                    permission_mode,
                    initial_branch,
                    current_branch if current_branch is not None else initial_branch,
                    status,
                    parent_session_id,
                    forked_from_turn_id,
                    created_at,
                    created_at,
                    created_at,
                    _json_dumps(payload),
                ),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(session_id)
        return self._session_from_row(row)

    def list_sessions(self, include_archived: bool = False) -> list[SessionSummary]:
        query = (
            "SELECT s.*, p.path AS project_path, "
            "(SELECT content_json FROM messages m WHERE m.session_id = s.session_id AND m.role = 'user' ORDER BY m.created_at DESC, m.message_id DESC LIMIT 1) AS last_user_content "
            "FROM sessions s JOIN projects p ON p.project_id = s.project_id"
        )
        params: tuple[Any, ...] = ()
        if not include_archived:
            query += " WHERE s.status = ?"
            params = ("active",)
        query += " ORDER BY s.last_activity_at DESC, s.created_at DESC, s.session_id DESC"
        with self.database.transaction() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._session_summary_from_row(row) for row in rows]

    def update_session(self, session_id: str, **changes: Any) -> SessionRecord:
        if not changes:
            return self.get_session(session_id)
        allowed = {
            "title",
            "provider",
            "current_model",
            "permission_mode",
            "initial_branch",
            "current_branch",
            "status",
            "parent_session_id",
            "forked_from_turn_id",
            "metadata",
            "updated_at",
            "last_activity_at",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported session fields: {sorted(invalid)}")
        payload = dict(changes)
        payload.setdefault("updated_at", now_iso())
        payload.setdefault("last_activity_at", payload["updated_at"])
        if "metadata" in payload:
            payload["metadata_json"] = _json_dumps(payload.pop("metadata"))
        columns = []
        values: list[Any] = []
        for key, value in payload.items():
            column = "metadata_json" if key == "metadata_json" else key
            columns.append(f"{column} = ?")
            values.append(value)
        values.append(session_id)
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE sessions SET {', '.join(columns)} WHERE session_id = ?", values)
        return self.get_session(session_id)

    def archive_session(self, session_id: str) -> SessionRecord:
        return self.update_session(session_id, status="archived")

    def unarchive_session(self, session_id: str) -> SessionRecord:
        return self.update_session(session_id, status="active")

    def create_turn(
        self,
        *,
        session_id: str,
        title: str,
        provider_snapshot: str,
        model_snapshot: str,
        permission_mode_snapshot: str,
        branch_snapshot: str | None,
        status: TurnStatus = "queued",
        metadata: dict[str, Any] | None = None,
    ) -> TurnRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, sequence, title, status, provider_snapshot, model_snapshot,
                    permission_mode_snapshot, branch_snapshot, created_at, updated_at, last_activity_at,
                    user_message_id, started_at, completed_at, error_code, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_turn_id(),
                    session_id,
                    sequence,
                    title,
                    status,
                    provider_snapshot,
                    model_snapshot,
                    permission_mode_snapshot,
                    branch_snapshot,
                    created_at,
                    created_at,
                    created_at,
                    None,
                    None,
                    None,
                    None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute(
                "SELECT * FROM turns WHERE session_id = ? AND sequence = ?",
                (session_id, sequence),
            ).fetchone()
        return self._turn_from_row(row)

    def update_turn_status(self, turn_id: str, status: TurnStatus) -> TurnRecord:
        updated_at = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turns SET status = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ?",
                (status, updated_at, updated_at, turn_id),
            )
            row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        return self._turn_from_row(row)

    def update_turn_metadata(self, turn_id: str, metadata: dict[str, Any]) -> TurnRecord:
        """把本次真实模型能力写入 Turn 快照，避免后续 registry 变化影响回放。"""

        with self.database.transaction() as connection:
            row = connection.execute("SELECT metadata_json FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if row is None:
                raise LookupError(turn_id)
            current = _json_loads(row["metadata_json"]) or {}
            current.update(metadata)
            connection.execute(
                "UPDATE turns SET metadata_json = ?, updated_at = ? WHERE turn_id = ?",
                (_json_dumps(current), now_iso(), turn_id),
            )
        return self.get_turn(turn_id)

    def list_turns(self, session_id: str) -> list[TurnRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [self._turn_from_row(row) for row in rows]

    def get_turn(self, turn_id: str) -> TurnRecord:
        """按稳定 ID 精确读取 Turn，避免依赖列表中的最后一条记录。"""

        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        if row is None:
            raise LookupError(turn_id)
        return self._turn_from_row(row)

    def create_attempt(
        self,
        *,
        turn_id: str,
        status: str = "created",
        started_at: str | None = None,
        ended_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunAttemptRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            attempt_number = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM run_attempts WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO run_attempts(
                    attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_attempt_id(),
                    turn_id,
                    attempt_number,
                    status,
                    created_at,
                    created_at,
                    started_at,
                    ended_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute(
                "SELECT * FROM run_attempts WHERE turn_id = ? AND attempt_number = ?",
                (turn_id, attempt_number),
            ).fetchone()
        return self._attempt_from_row(row)

    def update_attempt_status(self, attempt_id: str, status: str) -> RunAttemptRecord:
        now = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = ?, updated_at = ?, started_at = COALESCE(started_at, ?), ended_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled', 'interrupted') THEN ? ELSE ended_at END WHERE attempt_id = ?",
                (status, now, now, status, now, attempt_id),
            )
            row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return self._attempt_from_row(row)

    def get_attempt(self, attempt_id: str) -> RunAttemptRecord:
        """按稳定 ID 精确读取 Attempt。"""

        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise LookupError(attempt_id)
        return self._attempt_from_row(row)

    def list_attempts(self, turn_id: str) -> list[RunAttemptRecord]:
        """按创建顺序返回 Turn 的所有 Attempt，供恢复和 TUI 状态重建使用。"""

        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM run_attempts WHERE turn_id = ? ORDER BY attempt_number",
                (turn_id,),
            ).fetchall()
        return [self._attempt_from_row(row) for row in rows]

    def start_turn_attempt(self, turn_id: str, attempt_id: str, *, worker_id: str, lease_expires_at: str) -> tuple[TurnRecord, RunAttemptRecord]:
        """在模型调用前同时把 Turn 和指定 Attempt 标记为 running。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            attempt = connection.execute("SELECT turn_id FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
            if attempt is None:
                raise LookupError(attempt_id)
            if attempt["turn_id"] != turn_id:
                raise ValueError("attempt does not belong to turn")
            connection.execute(
                "UPDATE run_attempts SET status = 'running', started_at = ?, ended_at = NULL, interruption_reason = NULL, worker_id = ?, lease_expires_at = ?, updated_at = ? "
                "WHERE attempt_id = ? AND status = 'created'",
                (timestamp, worker_id, lease_expires_at, timestamp, attempt_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("attempt is not in created state")
            connection.execute(
                "UPDATE turns SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'queued'",
                (timestamp, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is not in queued state")
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = (SELECT session_id FROM turns WHERE turn_id = ?)",
                (timestamp, timestamp, turn_id),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if turn_row is None:
            raise LookupError(turn_id)
        return self._turn_from_row(turn_row), self._attempt_from_row(attempt_row)

    def finish_turn_attempt(
        self,
        turn_id: str,
        attempt_id: str,
        *,
        attempt_status: AttemptStatus,
        turn_status: TurnStatus,
        worker_id: str,
    ) -> tuple[TurnRecord, RunAttemptRecord]:
        """原子写入 Attempt、Turn 和 Session 的执行终态。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = ?, ended_at = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE attempt_id = ? AND turn_id = ? AND status = 'running' AND worker_id = ?",
                (attempt_status, timestamp, timestamp, attempt_id, turn_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise LookupError(attempt_id)
            connection.execute(
                "UPDATE turns SET status = ?, completed_at = ?, error_code = NULL, updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'running'",
                (turn_status, timestamp, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is no longer owned by this running attempt")
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = (SELECT session_id FROM turns WHERE turn_id = ?)",
                (timestamp, timestamp, turn_id),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return self._turn_from_row(turn_row), self._attempt_from_row(attempt_row)

    def interrupt_turn_attempt(self, turn_id: str, attempt_id: str, reason: str, *, worker_id: str) -> tuple[TurnRecord, RunAttemptRecord]:
        """未捕获异常时原子保留中断原因，不把未知执行结果误记为 failed。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = 'interrupted', ended_at = ?, interruption_reason = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE attempt_id = ? AND turn_id = ? AND status = 'running' AND worker_id = ?",
                (timestamp, reason, timestamp, attempt_id, turn_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise LookupError(attempt_id)
            connection.execute(
                "UPDATE turns SET status = 'interrupted', completed_at = ?, error_code = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'running'",
                (timestamp, reason, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is no longer owned by this running attempt")
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = (SELECT session_id FROM turns WHERE turn_id = ?)",
                (timestamp, timestamp, turn_id),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return self._turn_from_row(turn_row), self._attempt_from_row(attempt_row)

    def renew_attempt_lease(self, attempt_id: str, worker_id: str, lease_expires_at: str) -> None:
        """只允许当前 Worker 为自己的 running Attempt 续租。"""

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET lease_expires_at = ?, updated_at = ? WHERE attempt_id = ? AND worker_id = ? AND status = 'running'",
                (lease_expires_at, now_iso(), attempt_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("attempt lease is no longer owned by this worker")

    def create_turn_submission(
        self,
        *,
        session_id: str,
        text: str,
        actual_branch_reader: Callable[[], str | None],
        confirmed_branch: str | None,
        branch_confirmation_provided: bool,
    ) -> TurnSubmission | BranchConfirmationRequired:
        """在一个事务内提交分支确认、Turn、消息、Attempt 与领域事件。

        该方法是用户提交的唯一写入边界。事务开始后会再次读取 Session 分支和运行中
        Turn；任何校验失败或 SQL 异常都会整体回滚，不会留下半条业务事实。
        """

        timestamp = now_iso()
        turn_id = make_turn_id()
        attempt_id = make_attempt_id()
        message_id = make_message_id()
        with self.database.transaction() as connection:
            session_row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if session_row is None:
                raise LookupError(session_id)
            if session_row["status"] != "active":
                raise ValueError("archived session is read-only")
            if connection.execute(
                f"SELECT 1 FROM turns WHERE session_id = ? AND status IN ({','.join('?' for _ in BLOCKING_TURN_STATUSES)}) LIMIT 1",
                (session_id, *BLOCKING_TURN_STATUSES),
            ).fetchone() is not None:
                raise RuntimeError("session already has a running turn")
            if connection.execute(
                "SELECT 1 FROM permission_requests WHERE session_id = ? AND status = 'pending' LIMIT 1",
                (session_id,),
            ).fetchone() is not None:
                raise RuntimeError("session already has a pending permission request")
            if connection.execute(
                """
                SELECT 1
                FROM tool_calls tc
                JOIN turns t ON t.turn_id = tc.turn_id
                WHERE t.session_id = ?
                  AND tc.status IN ('approval_pending', 'execution_started', 'execution_uncertain', 'recovery_required')
                LIMIT 1
                """,
                (session_id,),
            ).fetchone() is not None:
                raise RuntimeError("session already has an unresolved tool call")

            # Git 是数据库之外的事实源，因此必须在持有本次提交事务时重新读取，不能使用
            # 弹窗出现前缓存的分支值完成确认。
            actual_branch = actual_branch_reader()
            old_branch = session_row["current_branch"]
            if old_branch != actual_branch and not branch_confirmation_provided:
                return BranchConfirmationRequired(session_id, old_branch, actual_branch)
            if branch_confirmation_provided and confirmed_branch != actual_branch:
                return BranchConfirmationRequired(session_id, confirmed_branch, actual_branch)

            # 先预留事件序列。branch_changed 必须在 Turn 行创建后写入，才能通过外键绑定
            # 生效 Turn；它仍早于 turn_created 事件，因此时间线语义不变。
            event_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            turn_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, sequence, title, status, provider_snapshot, model_snapshot,
                    permission_mode_snapshot, branch_snapshot, created_at, updated_at, last_activity_at,
                    user_message_id, started_at, completed_at, error_code, metadata_json
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    session_id,
                    turn_sequence,
                    f"Turn {turn_sequence}",
                    session_row["provider"],
                    session_row["current_model"],
                    session_row["permission_mode"],
                    actual_branch,
                    timestamp,
                    timestamp,
                    timestamp,
                    None,
                    None,
                    None,
                    None,
                    "{}",
                ),
            )
            connection.execute(
                "INSERT INTO run_attempts(attempt_id, turn_id, attempt_number, status, created_at, updated_at, started_at, ended_at, metadata_json) "
                "VALUES (?, ?, 1, 'created', ?, ?, NULL, NULL, ?)",
                (attempt_id, turn_id, timestamp, timestamp, "{}"),
            )
            if old_branch != actual_branch:
                event_sequence += 1
                connection.execute(
                    "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                    "VALUES (?, ?, ?, 'branch_changed', ?, ?, ?, ?, ?)",
                    (
                        make_event_id(),
                        session_id,
                        event_sequence,
                        timestamp,
                        turn_id,
                        attempt_id,
                        _json_dumps({"old_branch": old_branch, "new_branch": actual_branch, "effective_turn_sequence": turn_sequence}),
                        "{}",
                    ),
                )
            connection.execute(
                "INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) "
                "VALUES (?, ?, ?, NULL, 'user', 'completed', ?, ?, ?, NULL, ?)",
                (message_id, session_id, turn_id, _json_dumps(text), timestamp, timestamp, "{}"),
            )
            connection.execute(
                "UPDATE turns SET user_message_id = ? WHERE turn_id = ?",
                (message_id, turn_id),
            )

            first_user_message = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'user'",
                (session_id,),
            ).fetchone()[0] == 1
            title = _task_preview(text) if session_row["title"] == "New session" and first_user_message else session_row["title"]
            connection.execute(
                "UPDATE sessions SET title = ?, current_branch = ?, updated_at = ?, last_activity_at = ? WHERE session_id = ?",
                (title, actual_branch, timestamp, timestamp, session_id),
            )

            for event_type, payload in (
                ("turn_created", {"turn_id": turn_id}),
                ("user_message_created", {"text": text}),
            ):
                event_sequence += 1
                connection.execute(
                    "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        make_event_id(),
                        session_id,
                        event_sequence,
                        event_type,
                        timestamp,
                        turn_id,
                        attempt_id,
                        _json_dumps(payload),
                        "{}",
                    ),
                )

            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return TurnSubmission(self._turn_from_row(turn_row), self._attempt_from_row(attempt_row))

    def create_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        role: str,
        status: str,
        content: Any,
        attempt_id: str | None = None,
        interrupted_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageRecord:
        created_at = now_iso()
        message_id = make_message_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO messages(
                    message_id, session_id, turn_id, attempt_id, role, status, content_json,
                    created_at, updated_at, interrupted_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    turn_id,
                    attempt_id,
                    role,
                    status,
                    _json_dumps(content),
                    created_at,
                    created_at,
                    interrupted_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        return self._message_from_row(row)

    def append_message_part(
        self,
        message_id: str,
        *,
        type: str,
        content: Any,
        provider_format: str | None = None,
        replayable: bool = True,
        artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessagePartRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM message_parts WHERE message_id = ?",
                (message_id,),
            ).fetchone()[0]
            part_id = make_part_id()
            connection.execute(
                """
                INSERT INTO message_parts(
                    part_id, message_id, sequence, type, content_json, provider_format, replayable, created_at, artifact_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id,
                    message_id,
                    sequence,
                    type,
                    _json_dumps(content),
                    provider_format,
                    _bool_to_int(replayable),
                    created_at,
                    artifact_id,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM message_parts WHERE part_id = ?", (part_id,)).fetchone()
        return self._message_part_from_row(row)

    def update_message_status(self, message_id: str, status: str, interrupted_at: str | None = None) -> MessageRecord:
        updated_at = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE messages SET status = ?, updated_at = ?, interrupted_at = COALESCE(?, interrupted_at) WHERE message_id = ?",
                (status, updated_at, interrupted_at, message_id),
            )
            row = connection.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        return self._message_from_row(row)

    def list_messages_with_parts(self, session_id: str, turn_id: str | None = None) -> list[tuple[MessageRecord, list[MessagePartRecord]]]:
        query = "SELECT * FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        query += " ORDER BY created_at, message_id"
        with self.database.transaction() as connection:
            message_rows = connection.execute(query, params).fetchall()
            part_rows = connection.execute(
                "SELECT * FROM message_parts WHERE message_id IN (%s) ORDER BY message_id, sequence"
                % ",".join("?" for _ in message_rows),
                [row["message_id"] for row in message_rows],
            ).fetchall() if message_rows else []
        parts_by_message: dict[str, list[MessagePartRecord]] = {}
        for row in part_rows:
            part = self._message_part_from_row(row)
            parts_by_message.setdefault(part.message_id, []).append(part)
        return [(self._message_from_row(row), parts_by_message.get(row["message_id"], [])) for row in message_rows]

    def create_tool_call(
        self,
        *,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: ToolCallStatus = "created",
        attempt_id: str | None = None,
        message_id: str | None = None,
        side_effect: str | None = None,
        idempotency: str | None = None,
        recovery_strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallRecord:
        created_at = now_iso()
        tool_call_id = make_tool_call_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls(
                    tool_call_id, turn_id, attempt_id, message_id, status, tool_name, arguments_json,
                    created_at, updated_at, started_at, completed_at, side_effect, idempotency,
                    recovery_strategy, recovery_token_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    turn_id,
                    attempt_id,
                    message_id,
                    status,
                    tool_name,
                    _json_dumps(arguments),
                    created_at,
                    created_at,
                    None,
                    None,
                    side_effect,
                    idempotency,
                    recovery_strategy,
                    None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return self._tool_call_from_row(row)

    def mark_tool_approval_pending_with_event(self, tool_call_id: str, request: PermissionRequest) -> ToolCallRecord:
        return self._tool_call_from_row(self._mark_tool_call_status(tool_call_id, "approval_pending"))

    def mark_tool_approved(self, tool_call_id: str) -> ToolCallRecord:
        return self._tool_call_from_row(self._mark_tool_call_status(tool_call_id, "approved"))

    def mark_tool_execution_uncertain_with_event(self, tool_call_id: str, error: str) -> ToolCallRecord:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
            if row is None:
                raise LookupError(tool_call_id)
            connection.execute(
                "UPDATE tool_calls SET status = 'execution_uncertain', updated_at = ? WHERE tool_call_id = ?",
                (timestamp, tool_call_id),
            )
            turn_row = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (row["turn_id"],)).fetchone()
            if turn_row is None:
                raise LookupError(row["turn_id"])
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'tool_execution_uncertain', ?, ?, ?, ?, ?)",
                (
                    make_event_id(),
                    turn_row["session_id"],
                    turn_row["session_id"],
                    timestamp,
                    row["turn_id"],
                    row["attempt_id"],
                    _json_dumps({"tool_call_id": tool_call_id, "error": error}),
                    _json_dumps({}),
                ),
            )
            updated = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return self._tool_call_from_row(updated)

    def require_tool_recovery(
        self,
        turn_id: str,
        attempt_id: str,
        tool_call_id: str | None,
        reason: str,
        worker_id: str,
    ) -> tuple[TurnRecord, RunAttemptRecord]:
        """原子停止未知副作用 Attempt，并留下唯一的恢复入口事件。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE run_attempts SET status = 'interrupted', ended_at = ?, interruption_reason = ?, worker_id = NULL, lease_expires_at = NULL, updated_at = ? WHERE attempt_id = ? AND turn_id = ? AND status = 'running' AND worker_id = ?",
                (timestamp, reason, timestamp, attempt_id, turn_id, worker_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise LookupError(attempt_id)
            connection.execute(
                "UPDATE turns SET status = 'recovery_required', completed_at = ?, error_code = ?, updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'running'",
                (timestamp, reason, timestamp, timestamp, turn_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("turn is no longer owned by this running attempt")
            session_id = connection.execute("SELECT session_id FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()[0]
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, ?, 'recovery_required', ?, ?, ?, ?, ?)",
                (make_event_id(), session_id, sequence, timestamp, turn_id, attempt_id, _json_dumps({"tool_call_id": tool_call_id, "reason": reason}), _json_dumps({"worker_id": worker_id})),
            )
            turn_row = connection.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return self._turn_from_row(turn_row), self._attempt_from_row(attempt_row)

    def persist_recovered_tool_result(
        self,
        tool_call_id: str,
        *,
        status: ToolResultStatus,
        content: Any,
        output_preview: str | None = None,
        artifact_id: str | None = None,
        error: str | None = None,
        success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResultRecord:
        return self.persist_tool_result(
            tool_call_id,
            call_status="completed" if status in {"success", "recovered_completed"} else "failed",
            result_status=status,
            content=content,
            output_preview=output_preview,
            artifact_id=artifact_id,
            error=error,
            success=success,
            metadata=metadata,
        )

    def get_tool_call(self, tool_call_id: str) -> ToolCallRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        if row is None:
            raise LookupError(tool_call_id)
        return self._tool_call_from_row(row)

    def _mark_tool_call_status(self, tool_call_id: str, status: ToolCallStatus) -> Any:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = ?, updated_at = ? WHERE tool_call_id = ?",
                (status, now_iso(), tool_call_id),
            )
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        if row is None:
            raise LookupError(tool_call_id)
        return row

    def get_tool_result_by_call(self, tool_call_id: str) -> ToolResultRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tool_results WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return self._tool_result_from_row(row) if row is not None else None

    def list_tool_calls(self, session_id: str) -> list[ToolCallRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE turn_id IN (SELECT turn_id FROM turns WHERE session_id = ?) ORDER BY created_at, tool_call_id",
                (session_id,),
            ).fetchall()
        return [self._tool_call_from_row(row) for row in rows]

    def list_tool_results(self, session_id: str) -> list[ToolResultRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT tr.* FROM tool_results tr JOIN tool_calls tc ON tc.tool_call_id = tr.tool_call_id "
                "JOIN turns t ON t.turn_id = tc.turn_id WHERE t.session_id = ? ORDER BY tr.created_at, tr.tool_result_id",
                (session_id,),
            ).fetchall()
        return [self._tool_result_from_row(row) for row in rows]

    def list_permission_requests(self, session_id: str) -> list[PermissionRequestRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM permission_requests WHERE session_id = ? ORDER BY created_at, request_id",
                (session_id,),
            ).fetchall()
        return [self._permission_request_from_row(row) for row in rows]

    def mark_tool_execution_started(self, tool_call_id: str) -> ToolCallRecord:
        now = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = ?, started_at = ?, updated_at = ? WHERE tool_call_id = ?",
                ("execution_started", now, now, tool_call_id),
            )
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        return self._tool_call_from_row(row)

    def persist_tool_execution_started(self, tool_call_id: str, recovery_token: dict[str, Any]) -> ToolCallRecord:
        """在真实副作用前原子保存恢复 Token 和 execution_started 状态。"""

        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = 'execution_started', recovery_token_json = ?, started_at = ?, updated_at = ? WHERE tool_call_id = ?",
                (_json_dumps(recovery_token), timestamp, timestamp, tool_call_id),
            )
            row = connection.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
        if row is None:
            raise LookupError(tool_call_id)
        return self._tool_call_from_row(row)

    def persist_tool_result(
        self,
        tool_call_id: str,
        *,
        call_status: ToolCallStatus,
        result_status: ToolResultStatus,
        content: Any,
        output_preview: str | None = None,
        artifact_id: str | None = None,
        error: str | None = None,
        success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResultRecord:
        """按稳定 ToolCall ID 原子终结调用并写入唯一结果。"""

        timestamp = now_iso()
        result_id = make_tool_result_id()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = ?, completed_at = ?, updated_at = ? WHERE tool_call_id = ?",
                (call_status, timestamp, timestamp, tool_call_id),
            )
            connection.execute(
                "INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, output_preview, artifact_id, error, success, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    tool_call_id,
                    result_status,
                    _json_dumps(content),
                    timestamp,
                    output_preview,
                    artifact_id,
                    error,
                    _bool_to_int(success) if success is not None else None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM tool_results WHERE tool_result_id = ?", (result_id,)).fetchone()
        return self._tool_result_from_row(row)

    def create_tool_result(
        self,
        *,
        tool_call_id: str,
        status: ToolResultStatus,
        content: Any,
        output_preview: str | None = None,
        artifact_id: str | None = None,
        error: str | None = None,
        success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResultRecord:
        created_at = now_iso()
        result_id = make_tool_result_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tool_results(tool_result_id, tool_call_id, status, content_json, created_at, output_preview, artifact_id, error, success, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    tool_call_id,
                    status,
                    _json_dumps(content),
                    created_at,
                    output_preview,
                    artifact_id,
                    error,
                    _bool_to_int(success) if success is not None else None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM tool_results WHERE tool_result_id = ?", (result_id,)).fetchone()
        return self._tool_result_from_row(row)

    def list_unresolved_tool_calls(self, turn_id: str | None = None) -> list[ToolCallRecord]:
        query = "SELECT * FROM tool_calls WHERE status NOT IN ('completed', 'failed')"
        params: list[Any] = []
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(turn_id)
        query += " ORDER BY created_at, tool_call_id"
        with self.database.transaction() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._tool_call_from_row(row) for row in rows]

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
        attempt_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionEventRecord:
        created_at = now_iso()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            event_id = make_event_id()
            connection.execute(
                """
                INSERT INTO session_events(
                    event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    sequence,
                    event_type,
                    created_at,
                    turn_id,
                    attempt_id,
                    _json_dumps(payload),
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM session_events WHERE event_id = ?", (event_id,)).fetchone()
        return self._event_from_row(row)

    def list_events(self, session_id: str) -> list[SessionEventRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM session_events WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_context_summaries(self, session_id: str) -> list[ContextSummaryRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM context_summaries WHERE session_id = ? ORDER BY created_at, summary_id", (session_id,)).fetchall()
        return [self._context_summary_from_row(row) for row in rows]

    def get_latest_context_summary(self, session_id: str) -> ContextSummaryRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM context_summaries
                WHERE session_id = ? AND COALESCE(status, 'completed') = 'completed'
                ORDER BY COALESCE(source_end_sequence, -1) DESC, created_at DESC, summary_id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return self._context_summary_from_row(row) if row is not None else None

    def update_context_summary_status(self, summary_id: str, status: str) -> ContextSummaryRecord:
        with self.database.transaction() as connection:
            connection.execute("UPDATE context_summaries SET status = ? WHERE summary_id = ?", (status, summary_id))
            row = connection.execute("SELECT * FROM context_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        if row is None:
            raise LookupError(summary_id)
        return self._context_summary_from_row(row)

    def get_user_message_for_turn(self, turn_id: str) -> MessageRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE turn_id = ? AND role = 'user' ORDER BY created_at, message_id LIMIT 1",
                (turn_id,),
            ).fetchone()
        return self._message_from_row(row) if row is not None else None

    def get_permission_request(self, request_id: str) -> PermissionRequestRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise LookupError(request_id)
        return self._permission_request_from_row(row)

    def get_permission_response_by_request(self, request_id: str) -> PermissionResponseRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM permission_responses WHERE request_id = ? ORDER BY responded_at DESC, response_id DESC LIMIT 1", (request_id,)).fetchone()
        return self._permission_response_from_row(row) if row is not None else None

    def get_permission_grant(self, session_id: str, scope_key: str) -> PermissionGrantRecord | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM permission_grants WHERE session_id = ? AND scope_key = ? AND revoked_at IS NULL ORDER BY created_at DESC, grant_id DESC LIMIT 1",
                (session_id, scope_key),
            ).fetchone()
        return self._permission_grant_from_row(row) if row is not None else None

    def list_pending_permission_requests(self, session_id: str) -> list[PermissionRequestRecord]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM permission_requests WHERE session_id = ? AND status = 'pending' ORDER BY created_at, request_id",
                (session_id,),
            ).fetchall()
        return [self._permission_request_from_row(row) for row in rows]

    def create_permission_request(
        self,
        *,
        request_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        status: str,
        created_at: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        attempt_id: str | None = None,
        tool_call_id: str | None = None,
        scope_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PermissionRequestRecord:
        created_at = created_at or now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO permission_requests(
                    request_id, session_id, turn_id, attempt_id, tool_call_id, scope_key, tool_name,
                    arguments_json, reason, status, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    turn_id,
                    attempt_id,
                    tool_call_id,
                    scope_key,
                    tool_name,
                    _json_dumps(arguments),
                    reason,
                    status,
                    created_at,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)).fetchone()
        return self._permission_request_from_row(row)

    def persist_permission_request_and_pending_call(self, request: PermissionRequest) -> PermissionRequestRecord:
        """一次性写入 pending 权限请求和对应 ToolCall 的 pending 状态。"""

        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM permission_requests WHERE request_id = ?", (request.request_id,)).fetchone() is None:
                connection.execute(
                    """
                    INSERT INTO permission_requests(
                        request_id, session_id, turn_id, attempt_id, tool_call_id, scope_key, tool_name,
                        arguments_json, reason, status, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.request_id,
                        request.session_id,
                        request.turn_id,
                        request.attempt_id,
                        request.tool_call_id,
                        request.scope_key,
                        request.tool_name,
                        _json_dumps(request.arguments_preview),
                        request.reason,
                        request.status,
                        request.created_at,
                    _json_dumps(
                        {
                            "run_id": request.run_id,
                            "action_id": request.action_id,
                            "risk": request.risk,
                            "side_effect": request.side_effect,
                            "matched_rule": request.matched_rule,
                            "scope_json": request.scope_json,
                        }
                    ),
                ),
            )
            if request.tool_call_id is not None:
                connection.execute(
                    "UPDATE tool_calls SET status = 'approval_pending', updated_at = ? WHERE tool_call_id = ?",
                    (request.created_at, request.tool_call_id),
                )
            if request.turn_id is not None:
                # 权限请求、ToolCall 和 Turn 必须在同一事务中进入等待态，确保重启后
                # RecoveryService 看到的是完整状态，而不是仅有一条 pending 事件。
                connection.execute(
                    "UPDATE turns SET status = 'waiting_permission', updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'running'",
                    (request.created_at, request.created_at, request.turn_id),
                )
            if request.session_id is not None:
                connection.execute(
                    "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?",
                    (request.created_at, request.created_at, request.session_id),
                )
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'permission_pending', ?, ?, ?, ?, ?)",
                (
                    make_event_id(),
                    request.session_id,
                    request.session_id,
                    request.created_at,
                    request.turn_id,
                    request.attempt_id,
                    _json_dumps(
                        {
                            "request_id": request.request_id,
                            "tool_name": request.tool_name,
                            "tool_call_id": request.tool_call_id,
                            "scope_key": request.scope_key,
                        }
                    ),
                    _json_dumps({"source": "permission_broker"}),
                ),
            )
            row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request.request_id,)).fetchone()
        return self._permission_request_from_row(row)

    def persist_permission_resolution(
        self,
        request_id: str,
        decision: str,
        reason: str | None,
        *,
        create_grant: bool,
        source: str,
    ) -> PermissionResponseRecord:
        """原子写入权限响应、请求状态、Grant 和唯一领域事件。"""

        responded_at = permission_now_iso()
        with self.database.transaction() as connection:
            request_row = connection.execute("SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)).fetchone()
            if request_row is None:
                raise LookupError(request_id)
            request = self._permission_request_from_row(request_row)
            existing = connection.execute("SELECT * FROM permission_responses WHERE request_id = ? ORDER BY responded_at DESC, response_id DESC LIMIT 1", (request_id,)).fetchone()
            if existing is not None:
                return self._permission_response_from_row(existing)
            if request.status != "pending":
                raise RuntimeError("permission request is no longer pending")
            response_id = f"response-{request_id}"
            connection.execute(
                """
                INSERT INTO permission_responses(response_id, request_id, decision, reason, responded_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    response_id,
                    request_id,
                    decision,
                    reason,
                    responded_at,
                    _json_dumps({"source": source}),
                ),
            )
            request_status = "approved" if decision in {"approve_once", "approve_session"} else "denied"
            connection.execute(
                "UPDATE permission_requests SET status = ? WHERE request_id = ?",
                (request_status, request_id),
            )
            grant_id: str | None = None
            if create_grant and decision == "approve_session" and request.scope_key is not None and request.session_id is not None:
                grant_row = connection.execute(
                    "SELECT * FROM permission_grants WHERE session_id = ? AND scope_key = ? AND revoked_at IS NULL ORDER BY created_at DESC, grant_id DESC LIMIT 1",
                    (request.session_id, request.scope_key),
                ).fetchone()
                if grant_row is None:
                    grant_id = _make_local_id("grant")
                    connection.execute(
                        """
                        INSERT INTO permission_grants(grant_id, session_id, scope_key, tool_name, scope_json, created_at, revoked_at, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                        """,
                        (
                            grant_id,
                            request.session_id,
                            request.scope_key,
                            request.tool_name,
                            _json_dumps(request.metadata.get("scope_json")) if request.metadata.get("scope_json") is not None else None,
                            responded_at,
                            _json_dumps({"request_id": request_id, "source": source}),
                        ),
                    )
                else:
                    grant_id = str(grant_row["grant_id"])
            if request.tool_call_id is not None:
                connection.execute(
                    "UPDATE tool_calls SET status = ?, completed_at = CASE WHEN ? = 'denied' THEN COALESCE(completed_at, ?) ELSE NULL END, updated_at = ? WHERE tool_call_id = ?",
                    (
                        "approved" if decision in {"approve_once", "approve_session"} else "denied",
                        "approved" if decision in {"approve_once", "approve_session"} else "denied",
                        responded_at,
                        responded_at,
                        request.tool_call_id,
                    ),
                )
            if request.turn_id is not None:
                # 活跃 Worker 在收到响应后仍需写入 ToolResult 或继续执行，所以审批完成先
                # 恢复 running；重启恢复路径会随后由 RecoveryService 原子创建新 Attempt。
                connection.execute(
                    "UPDATE turns SET status = 'running', updated_at = ?, last_activity_at = ? WHERE turn_id = ? AND status = 'waiting_permission'",
                    (responded_at, responded_at, request.turn_id),
                )
            if request.session_id is not None:
                connection.execute(
                    "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?",
                    (responded_at, responded_at, request.session_id),
                )
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?), 'permission_resolved', ?, ?, ?, ?, ?)",
                (
                    make_event_id(),
                    request.session_id,
                    request.session_id,
                    responded_at,
                    request.turn_id,
                    request.attempt_id,
                    _json_dumps(
                        {
                            "request_id": request_id,
                            "decision": decision,
                            "reason": reason,
                            "source": source,
                            "scope_key": request.scope_key,
                            "grant_id": grant_id,
                            "tool_call_id": request.tool_call_id,
                        }
                    ),
                    _json_dumps({}),
                ),
            )
            row = connection.execute("SELECT * FROM permission_responses WHERE request_id = ? ORDER BY responded_at DESC, response_id DESC LIMIT 1", (request_id,)).fetchone()
        return self._permission_response_from_row(row)

    def create_permission_response(
        self,
        *,
        response_id: str,
        request_id: str,
        decision: str,
        reason: str | None,
        responded_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PermissionResponseRecord:
        responded_at = responded_at or now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO permission_responses(response_id, request_id, decision, reason, responded_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (response_id, request_id, decision, reason, responded_at, _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM permission_responses WHERE response_id = ?", (response_id,)).fetchone()
        return self._permission_response_from_row(row)

    def create_permission_grant(
        self,
        *,
        session_id: str,
        scope_key: str,
        tool_name: str | None = None,
        scope_json: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        revoked_at: str | None = None,
    ) -> PermissionGrantRecord:
        created_at = now_iso()
        grant_id = _make_local_id("grant")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO permission_grants(grant_id, session_id, scope_key, tool_name, scope_json, created_at, revoked_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (grant_id, session_id, scope_key, tool_name, _json_dumps(scope_json) if scope_json is not None else None, created_at, revoked_at, _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM permission_grants WHERE grant_id = ?", (grant_id,)).fetchone()
        return self._permission_grant_from_row(row)

    def create_context_summary(
        self,
        *,
        session_id: str,
        content: Any,
        turn_id: str | None = None,
        source_start_sequence: int | None = None,
        source_end_sequence: int | None = None,
        summary_message_id: str | None = None,
        model: str | None = None,
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
    ) -> ContextSummaryRecord:
        created_at = now_iso()
        summary_id = _make_local_id("summary")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO context_summaries(
                    summary_id, session_id, turn_id, created_at, content_json,
                    source_start_sequence, source_end_sequence, summary_message_id, model, status, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary_id,
                    session_id,
                    turn_id,
                    created_at,
                    _json_dumps(content),
                    source_start_sequence,
                    source_end_sequence,
                    summary_message_id,
                    model,
                    status,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM context_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        return self._context_summary_from_row(row)

    def create_context_summary_with_message(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        content: str,
        source_start_sequence: int | None,
        source_end_sequence: int | None,
        model: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextSummaryRecord:
        """在同一事务中写入 Summary Message、Summary Part 和摘要索引。"""

        timestamp = now_iso()
        message_id = make_message_id()
        part_id = make_part_id()
        summary_id = _make_local_id("summary")
        with self.database.transaction() as connection:
            summary_turn_id = turn_id or connection.execute(
                "SELECT turn_id FROM turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) VALUES (?, ?, ?, NULL, 'system', 'completed', ?, ?, ?, NULL, ?)",
                (message_id, session_id, summary_turn_id, _json_dumps(content), timestamp, timestamp, _json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO message_parts(part_id, message_id, sequence, type, content_json, provider_format, replayable, created_at, artifact_id, metadata_json) VALUES (?, ?, 1, 'summary', ?, NULL, 1, ?, NULL, ?)",
                (part_id, message_id, _json_dumps(content), timestamp, _json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO context_summaries(summary_id, session_id, turn_id, created_at, content_json, source_start_sequence, source_end_sequence, summary_message_id, model, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)",
                (summary_id, session_id, summary_turn_id, timestamp, _json_dumps(content), source_start_sequence, source_end_sequence, message_id, model, _json_dumps(metadata or {})),
            )
            row = connection.execute("SELECT * FROM context_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        return self._context_summary_from_row(row)

    def replace_context_summary(
        self,
        *,
        session_id: str,
        previous_summary_id: str | None,
        summary_content: str,
        turn_id: str | None,
        source_start_sequence: int | None,
        source_end_sequence: int | None,
        model: str | None,
        metadata: dict[str, Any] | None,
        event_payload: dict[str, Any],
    ) -> ContextSummaryRecord:
        """原子替换摘要、摘要消息和 Compact Event。

        旧摘要只有在新消息、新摘要索引和事件都成功写入后才会标记为 superseded；
        任意 SQL 失败都会由外层事务回滚，保证数据库中始终至少有一个有效摘要。
        """

        timestamp = now_iso()
        message_id = make_message_id()
        part_id = make_part_id()
        summary_id = _make_local_id("summary")
        with self.database.transaction() as connection:
            if previous_summary_id is not None:
                previous = connection.execute(
                    "SELECT status FROM context_summaries WHERE summary_id = ? AND session_id = ?",
                    (previous_summary_id, session_id),
                ).fetchone()
                if previous is None or previous["status"] not in {None, "completed"}:
                    raise RuntimeError("previous context summary is no longer completed")
            summary_turn_id = turn_id or connection.execute(
                "SELECT turn_id FROM turns WHERE session_id = ? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(message_id, session_id, turn_id, attempt_id, role, status, content_json, created_at, updated_at, interrupted_at, metadata_json) VALUES (?, ?, ?, NULL, 'system', 'completed', ?, ?, ?, NULL, ?)",
                (message_id, session_id, summary_turn_id, _json_dumps(summary_content), timestamp, timestamp, _json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO message_parts(part_id, message_id, sequence, type, content_json, provider_format, replayable, created_at, artifact_id, metadata_json) VALUES (?, ?, 1, 'summary', ?, NULL, 1, ?, NULL, ?)",
                (part_id, message_id, _json_dumps(summary_content), timestamp, _json_dumps({"summary_id": summary_id})),
            )
            connection.execute(
                "INSERT INTO context_summaries(summary_id, session_id, turn_id, created_at, content_json, source_start_sequence, source_end_sequence, summary_message_id, model, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)",
                (summary_id, session_id, summary_turn_id, timestamp, _json_dumps(summary_content), source_start_sequence, source_end_sequence, message_id, model, _json_dumps(metadata or {})),
            )
            connection.execute(
                "UPDATE context_summaries SET status = 'superseded' WHERE session_id = ? AND COALESCE(status, 'completed') = 'completed' AND summary_id != ?",
                (session_id, summary_id),
            )
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO session_events(event_id, session_id, sequence, event_type, created_at, turn_id, attempt_id, payload_json, metadata_json) VALUES (?, ?, ?, 'context_compacted', ?, ?, NULL, ?, ?)",
                (make_event_id(), session_id, sequence, timestamp, turn_id, _json_dumps(event_payload | {"summary_id": summary_id}), _json_dumps({"source": "compaction_service"})),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?",
                (timestamp, timestamp, session_id),
            )
            row = connection.execute("SELECT * FROM context_summaries WHERE summary_id = ?", (summary_id,)).fetchone()
        return self._context_summary_from_row(row)

    def create_artifact(
        self,
        *,
        session_id: str,
        kind: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        storage_path: str,
        artifact_id: str | None = None,
        content: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        created_at = now_iso()
        artifact_id = artifact_id or make_artifact_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, session_id, kind, mime_type, size_bytes, sha256, storage_path,
                    created_at, content_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    session_id,
                    kind,
                    mime_type,
                    size_bytes,
                    sha256,
                    storage_path,
                    created_at,
                    _json_dumps(content) if content is not None else None,
                    _json_dumps(metadata or {}),
                ),
            )
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return self._artifact_from_row(row)

    def _project_from_row(self, row: Any) -> ProjectRecord:
        data = _row_dict(row)
        return ProjectRecord(project_id=data["project_id"], path=Path(data["path"]), created_at=data["created_at"], updated_at=data["updated_at"])

    def _session_from_row(self, row: Any) -> SessionRecord:
        data = _row_dict(row)
        return SessionRecord(
            session_id=data["session_id"],
            project_id=data["project_id"],
            title=data["title"],
            provider=data["provider"],
            current_model=data["current_model"],
            permission_mode=data["permission_mode"],
            initial_branch=data["initial_branch"],
            current_branch=data["current_branch"],
            status=data["status"],
            parent_session_id=data["parent_session_id"],
            forked_from_turn_id=data["forked_from_turn_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            last_activity_at=data["last_activity_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _session_summary_from_row(self, row: Any) -> SessionSummary:
        data = _row_dict(row)
        return SessionSummary(
            session_id=data["session_id"],
            project_id=data["project_id"],
            title=data["title"],
            provider=data["provider"],
            current_model=data["current_model"],
            permission_mode=data["permission_mode"],
            status=data["status"],
            current_branch=data["current_branch"],
            last_activity_at=data["last_activity_at"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            project_path=Path(data["project_path"]),
            project_exists=Path(data["project_path"]).exists(),
            last_user_preview=_content_preview(_json_loads(data["last_user_content"])) if data.get("last_user_content") is not None else None,
        )

    def _turn_from_row(self, row: Any) -> TurnRecord:
        data = _row_dict(row)
        return TurnRecord(
            turn_id=data["turn_id"],
            session_id=data["session_id"],
            sequence=data["sequence"],
            title=data["title"],
            status=data["status"],
            provider_snapshot=data["provider_snapshot"],
            model_snapshot=data["model_snapshot"],
            permission_mode_snapshot=data["permission_mode_snapshot"],
            branch_snapshot=data["branch_snapshot"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            last_activity_at=data["last_activity_at"],
            user_message_id=data.get("user_message_id"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error_code=data.get("error_code"),
            metadata=_json_loads(data["metadata_json"]),
        )

    def _attempt_from_row(self, row: Any) -> RunAttemptRecord:
        data = _row_dict(row)
        return RunAttemptRecord(
            attempt_id=data["attempt_id"],
            turn_id=data["turn_id"],
            attempt_number=data["attempt_number"],
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data["started_at"],
            ended_at=data["ended_at"],
            interruption_reason=data["interruption_reason"],
            worker_id=data["worker_id"],
            lease_expires_at=data["lease_expires_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _message_from_row(self, row: Any) -> MessageRecord:
        data = _row_dict(row)
        return MessageRecord(
            message_id=data["message_id"],
            session_id=data["session_id"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            role=data["role"],
            status=data["status"],
            content=_json_loads(data["content_json"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            interrupted_at=data["interrupted_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _message_part_from_row(self, row: Any) -> MessagePartRecord:
        data = _row_dict(row)
        return MessagePartRecord(
            part_id=data["part_id"],
            message_id=data["message_id"],
            sequence=data["sequence"],
            type=data["type"],
            content=_json_loads(data["content_json"]),
            provider_format=data["provider_format"],
            replayable=_int_to_bool(data["replayable"]),
            created_at=data["created_at"],
            artifact_id=data["artifact_id"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _tool_call_from_row(self, row: Any) -> ToolCallRecord:
        data = _row_dict(row)
        return ToolCallRecord(
            tool_call_id=data["tool_call_id"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            message_id=data["message_id"],
            status=data["status"],
            tool_name=data["tool_name"],
            arguments=_json_loads(data["arguments_json"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data["started_at"],
            completed_at=data["completed_at"],
            side_effect=data["side_effect"],
            idempotency=data["idempotency"],
            recovery_strategy=data["recovery_strategy"],
            recovery_token=_json_loads(data["recovery_token_json"]) if data["recovery_token_json"] is not None else None,
            metadata=_json_loads(data["metadata_json"]),
        )

    def _tool_result_from_row(self, row: Any) -> ToolResultRecord:
        data = _row_dict(row)
        return ToolResultRecord(
            tool_result_id=data["tool_result_id"],
            tool_call_id=data["tool_call_id"],
            status=data["status"],
            content=_json_loads(data["content_json"]),
            created_at=data["created_at"],
            output_preview=data["output_preview"],
            artifact_id=data["artifact_id"],
            error=data["error"],
            success=_int_to_bool(data["success"]) if data["success"] is not None else None,
            metadata=_json_loads(data["metadata_json"]),
        )

    def _permission_request_from_row(self, row: Any) -> PermissionRequestRecord:
        data = _row_dict(row)
        return PermissionRequestRecord(
            request_id=data["request_id"],
            session_id=data["session_id"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            tool_call_id=data["tool_call_id"],
            scope_key=data["scope_key"],
            tool_name=data["tool_name"],
            arguments=_json_loads(data["arguments_json"]),
            reason=data["reason"],
            status=data["status"],
            created_at=data["created_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _permission_response_from_row(self, row: Any) -> PermissionResponseRecord:
        data = _row_dict(row)
        return PermissionResponseRecord(
            response_id=data["response_id"],
            request_id=data["request_id"],
            decision=data["decision"],
            reason=data["reason"],
            responded_at=data["responded_at"],
            metadata=_json_loads(data["metadata_json"]),
        )

    def _permission_grant_from_row(self, row: Any) -> PermissionGrantRecord:
        data = _row_dict(row)
        return PermissionGrantRecord(
            grant_id=data["grant_id"],
            session_id=data["session_id"],
            scope_key=data["scope_key"],
            created_at=data["created_at"],
            revoked_at=data["revoked_at"],
            tool_name=data["tool_name"],
            scope_json=_json_loads(data["scope_json"]) if data["scope_json"] is not None else None,
            metadata=_json_loads(data["metadata_json"]),
        )

    def _event_from_row(self, row: Any) -> SessionEventRecord:
        data = _row_dict(row)
        return SessionEventRecord(
            event_id=data["event_id"],
            session_id=data["session_id"],
            sequence=data["sequence"],
            event_type=data["event_type"],
            created_at=data["created_at"],
            turn_id=data["turn_id"],
            attempt_id=data["attempt_id"],
            payload=_json_loads(data["payload_json"]),
            metadata=_json_loads(data["metadata_json"]),
        )

    def _context_summary_from_row(self, row: Any) -> ContextSummaryRecord:
        data = _row_dict(row)
        return ContextSummaryRecord(
            summary_id=data["summary_id"],
            session_id=data["session_id"],
            turn_id=data["turn_id"],
            created_at=data["created_at"],
            content=_json_loads(data["content_json"]),
            source_start_sequence=data.get("source_start_sequence"),
            source_end_sequence=data.get("source_end_sequence"),
            summary_message_id=data.get("summary_message_id"),
            model=data.get("model"),
            status=data.get("status") or "completed",
            metadata=_json_loads(data["metadata_json"]),
        )

    def _artifact_from_row(self, row: Any) -> ArtifactRecord:
        data = _row_dict(row)
        return ArtifactRecord(
            artifact_id=data["artifact_id"],
            session_id=data["session_id"],
            kind=data["kind"],
            mime_type=data["mime_type"],
            size_bytes=data["size_bytes"],
            sha256=data["sha256"],
            storage_path=data["storage_path"],
            created_at=data["created_at"],
            content=_json_loads(data["content_json"]) if data["content_json"] is not None else None,
            metadata=_json_loads(data["metadata_json"]),
        )
