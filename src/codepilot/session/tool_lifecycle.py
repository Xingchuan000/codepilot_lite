from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from codepilot.router.actions import ToolAction
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.ids import now_iso
from codepilot.session.models import to_jsonable
from codepilot.session.reconcilers import shell_command_is_read_only
from codepilot.session.store import SessionStore
from codepilot.tools.base import ToolResult, ToolSpec


class SQLiteToolLifecycleObserver:
    """用稳定 ToolCall ID 维护 SQLite 工具业务事实。

    Router 必须等待每个方法提交后才能进入下一阶段，因此真实副作用只会发生在恢复
    Token 和 `execution_started` 已经持久化之后。
    """

    def __init__(self, database: SessionDatabase, session_id: str, turn_id: str, attempt_id: str) -> None:
        self.database = database
        self.store = SessionStore(database)
        self.artifacts = ArtifactStore(database)
        self.session_id = session_id
        self.turn_id = turn_id
        self.attempt_id = attempt_id

    def on_tool_call_created(self, action: ToolAction, spec: ToolSpec | None) -> str:
        record = self.store.create_tool_call(
            turn_id=self.turn_id,
            attempt_id=self.attempt_id,
            tool_name=action.tool_name,
            arguments=to_jsonable(action.arguments),
            side_effect=spec.side_effect.value if spec is not None else None,
            idempotency=spec.idempotency.value if spec is not None else None,
            recovery_strategy=spec.recovery_strategy.value if spec is not None else None,
            metadata={"action_id": action.action_id},
        )
        return record.tool_call_id

    def on_policy_denied(self, tool_call_id: str, result: ToolResult) -> None:
        persisted = self.artifacts.persist_content(self.session_id, "tool_result", result.error or "policy denied")
        self.store.persist_tool_result(
            tool_call_id,
            call_status="denied",
            result_status="denied",
            content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
            output_preview=persisted.preview,
            artifact_id=persisted.artifact_id,
            error=result.error,
            success=False,
            metadata={**result.metadata, "executed": False},
        )

    def on_permission_pending(self, tool_call_id: str, request: Any) -> None:
        timestamp = now_iso()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = 'approval_pending', updated_at = ? WHERE tool_call_id = ?",
                (timestamp, tool_call_id),
            )
        self.store.append_event(
            session_id=self.session_id,
            event_type="permission_pending",
            payload={"request_id": request.request_id, "tool_name": request.tool_name, "tool_call_id": tool_call_id},
            turn_id=self.turn_id,
            attempt_id=self.attempt_id,
        )

    def on_permission_resolved(self, tool_call_id: str, request: Any, response: Any, result: ToolResult | None = None) -> None:
        self.store.append_event(
            session_id=self.session_id,
            event_type="permission_resolved",
            payload={
                "request_id": request.request_id,
                "decision": response.decision if response is not None else "deny",
                "tool_call_id": tool_call_id,
            },
            turn_id=self.turn_id,
            attempt_id=self.attempt_id,
        )
        if result is not None:
            persisted = self.artifacts.persist_content(self.session_id, "tool_result", result.error or "permission denied")
            self.store.persist_tool_result(
                tool_call_id,
                call_status="denied",
                result_status="denied",
                content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
                output_preview=persisted.preview,
                artifact_id=persisted.artifact_id,
                error=result.error,
                success=False,
                metadata={**result.metadata, "executed": False},
            )
            return
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = 'approved', updated_at = ? WHERE tool_call_id = ?",
                (now_iso(), tool_call_id),
            )

    def build_recovery_token(self, action: ToolAction, spec: ToolSpec | None) -> dict[str, Any]:
        """根据执行前事实生成 Token；不从恢复时的当前内容反推原状态。"""

        arguments = action.arguments
        token: dict[str, Any] = {
            "tool_name": action.tool_name,
            "arguments_sha256": _sha256_json(arguments),
            "side_effect": spec.side_effect.value if spec is not None else None,
            "idempotency": spec.idempotency.value if spec is not None else None,
            "recovery_strategy": spec.recovery_strategy.value if spec is not None else None,
        }
        if action.tool_name == "replace_range":
            repo = Path(arguments["repo"]).expanduser().resolve()
            path = (repo / str(arguments["path"])).resolve()
            if not path.is_relative_to(repo):
                raise ValueError("Path escapes repository root")
            old_bytes = path.read_bytes()
            old_text = old_bytes.decode("utf-8", errors="replace")
            lines = old_text.splitlines(keepends=True)
            start = int(arguments["start_line"]) - 1
            end = int(arguments["end_line"])
            expected_text = "".join(lines[:start]) + str(arguments["replacement"]) + "".join(lines[end:])
            token.update(
                {
                    "path": str(path),
                    "pre_file_sha256": _sha256_bytes(old_bytes),
                    "expected_file_sha256": _sha256_bytes(expected_text.encode("utf-8")),
                    "file_existed": True,
                    "start_line": int(arguments["start_line"]),
                    "end_line": int(arguments["end_line"]),
                    "replacement_sha256": _sha256_bytes(str(arguments["replacement"]).encode("utf-8")),
                }
            )
        elif action.tool_name == "apply_patch":
            repo = Path(arguments["repo"]).expanduser().resolve()
            patch = str(arguments["patch"])
            forward = subprocess.run(
                ["git", "-C", str(repo), "apply", "--check", "-"],
                input=patch,
                text=True,
                capture_output=True,
                check=False,
            )
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=False,
            )
            token.update(
                {
                    "patch_sha256": _sha256_bytes(patch.encode("utf-8")),
                    "repo": str(repo),
                    "baseline_head": head.stdout.strip() if head.returncode == 0 else None,
                    "forward_check_before": forward.returncode == 0,
                }
            )
        elif action.tool_name == "run_shell":
            command = str(arguments["command"])
            token.update(
                {
                    "repo": str(Path(arguments["repo"]).expanduser().resolve()),
                    "command_sha256": _sha256_bytes(command.encode("utf-8")),
                    "auto_retry_allowed": shell_command_is_read_only(command),
                }
            )
        return token

    def on_execution_started(self, tool_call_id: str, recovery_token: dict[str, Any]) -> None:
        self.store.persist_tool_execution_started(tool_call_id, recovery_token)

    def on_pre_execution_failure(self, tool_call_id: str, error: Exception) -> None:
        """Token 构建失败发生在副作用前，明确写 failed 而不是 uncertain。"""

        persisted = self.artifacts.persist_content(self.session_id, "tool_result", str(error))
        self.store.persist_tool_result(
            tool_call_id,
            call_status="failed",
            result_status="failed",
            content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
            output_preview=persisted.preview,
            artifact_id=persisted.artifact_id,
            error=str(error),
            success=False,
            metadata={"executed": False, "phase": "recovery_token"},
        )

    def on_execution_finished(self, tool_call_id: str, result: ToolResult) -> None:
        persisted = self.artifacts.persist_content(self.session_id, "tool_result", result.output or result.error or "")
        self.store.persist_tool_result(
            tool_call_id,
            call_status="completed" if result.success else "failed",
            result_status="success" if result.success else "failed",
            content=persisted.inline_content if persisted.inline_content is not None else persisted.preview,
            output_preview=persisted.preview,
            artifact_id=persisted.artifact_id,
            error=result.error,
            success=result.success,
            metadata=result.metadata,
        )

    def on_execution_exception(self, tool_call_id: str, error: Exception) -> None:
        """保留无结果的 uncertain 调用，交由 RecoveryService 对账。"""

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE tool_calls SET status = 'execution_uncertain', updated_at = ? WHERE tool_call_id = ?",
                (now_iso(), tool_call_id),
            )
        self.store.append_event(
            session_id=self.session_id,
            event_type="tool_execution_uncertain",
            payload={"tool_call_id": tool_call_id, "error": str(error)},
            turn_id=self.turn_id,
            attempt_id=self.attempt_id,
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
