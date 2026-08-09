from __future__ import annotations

import fnmatch
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from codepilot.common.patches import extract_paths_from_patch
from codepilot.multi_agent.models import AgentHandle, AgentStatus, SpawnContract
from codepilot.multi_agent.profiles import get_agent_profile
from codepilot.multi_agent.workspace import AgentWorkspace, create_agent_worktree, persist_agent_patch
from codepilot.policy import PolicyChecker, PolicyContext
from codepilot.repo.worktree import remove_issue_worktree
from codepilot.router.actions import ToolAction
from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.models import BranchConfirmationRequired, SessionRecord
from codepilot.session.service import SessionService
from codepilot.session.store import SessionStore
from codepilot.tools.base import ToolResult
from codepilot.tools.edit_tools import apply_patch as apply_patch_tool

ChildRuntimeFactory = Callable[[str], Any]
AgentEventSink = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class AgentSupervisorConfig:
    max_active_children: int = 3
    max_active_writers: int = 2
    max_depth: int = 1
    wait_timeout_seconds: float = 30.0


@dataclass
class _RunningChild:
    handle: AgentHandle
    thread: threading.Thread
    cancel_event: threading.Event
    workspace: AgentWorkspace | None = None


def _normalize_scope(scope: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in scope:
        value = raw.strip().replace("\\", "/")
        if not value:
            raise ValueError("write_scope entries must not be empty")
        if value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ValueError("write_scope must contain repository-relative paths without '..'")
        value = str(PurePosixPath(value.removeprefix("./")))
        if value == ".":
            raise ValueError("write_scope must not contain the repository root")
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def scopes_may_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    for a in left:
        for b in right:
            if a == b:
                return True
            if "**" in a or "**" in b or "*" in a or "*" in b:
                return True
            a_prefix = a.rstrip("/") + "/"
            b_prefix = b.rstrip("/") + "/"
            if a.startswith(b_prefix) or b.startswith(a_prefix):
                return True
    return False


def path_allowed(path: str, scope: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").removeprefix("./")
    if not normalized or normalized.startswith("/") or ".." in PurePosixPath(normalized).parts:
        return False
    return any(
        fnmatch.fnmatch(normalized, pattern)
        or normalized == pattern.rstrip("/")
        or normalized.startswith(pattern.rstrip("/") + "/")
        for pattern in scope
    )


class AgentSupervisor:
    def __init__(
        self,
        *,
        database: SessionDatabase,
        child_runtime_factory: ChildRuntimeFactory,
        event_sink: AgentEventSink | None = None,
        config: AgentSupervisorConfig | None = None,
    ) -> None:
        self.database = database
        self.store = SessionStore(database)
        self.service = SessionService(database)
        self.artifacts = ArtifactStore(database)
        self.child_runtime_factory = child_runtime_factory
        self.event_sink = event_sink
        self.config = config or AgentSupervisorConfig()
        self._lock = threading.RLock()
        self._running: dict[str, _RunningChild] = {}

    def spawn(self, *, context: Any, contract: SpawnContract) -> dict[str, object]:
        with self._lock:
            parent = self._assert_primary_parent(context.parent_session_id)
            parent_turn = self.store.get_turn(context.parent_turn_id)
            if parent_turn.session_id != parent.session_id:
                raise ValueError("parent turn does not belong to parent session")
            parent_depth = int(parent.metadata.get("agent_depth", 0))
            if parent.metadata.get("agent_type") is not None or parent_depth >= self.config.max_depth:
                raise PermissionError("only the Primary session may spawn children")
            profile = get_agent_profile(contract.agent_type)
            if not contract.task.strip():
                raise ValueError("agent task must not be empty")
            write_scope = _normalize_scope(tuple(contract.write_scope))
            if write_scope and contract.agent_type != "general":
                raise PermissionError("only a general agent may receive write_scope")
            if write_scope and not profile.supports_write:
                raise PermissionError(f"agent profile {contract.agent_type} cannot write")
            if write_scope and (
                parent.permission_mode == "read_only"
                or parent_turn.permission_mode_snapshot == "read_only"
            ):
                raise PermissionError("read-only parent cannot delegate a writable child")
            if len(self._active_children(parent.session_id)) >= self.config.max_active_children:
                raise RuntimeError("maximum active child count reached")
            if write_scope:
                writers = self._active_writers(parent.session_id)
                if len(writers) >= self.config.max_active_writers:
                    raise RuntimeError("maximum active writer count reached")
                if any(scopes_may_overlap(write_scope, item) for item in writers):
                    raise RuntimeError("write_scope overlaps another active general agent")

            opened_parent = self.service.open_session(parent.session_id)
            workspace = create_agent_worktree(opened_parent.project_path) if write_scope else None
            metadata: dict[str, object] = {
                "agent_type": contract.agent_type,
                "agent_depth": parent_depth + 1,
                "agent_task": contract.task,
                "write_scope": list(write_scope),
                "memory_write": False,
                "agent_status": "queued",
                "context_fork": {
                    "mode": contract.context_mode,
                    "recent_turns": contract.recent_turns,
                },
                "source_project_path": str(opened_parent.project_path),
            }
            if workspace is not None:
                metadata.update(
                    {
                        "workspace_path": str(workspace.path),
                        "workspace_run_id": workspace.run_id,
                        "workspace_branch": workspace.branch,
                    }
                )
            try:
                child = self.service.create_child_session(
                    parent_session_id=parent.session_id,
                    forked_from_turn_id=parent_turn.turn_id,
                    provider=parent.provider,
                    model=parent.current_model,
                    permission_mode=parent.permission_mode,
                    metadata=metadata,
                )
            except Exception:
                if workspace is not None:
                    remove_issue_worktree(
                        workspace.path,
                        original_repo=workspace.original_repo,
                        branch_name=workspace.branch,
                        force=True,
                    )
                raise
            handle = AgentHandle(
                child_session_id=child.session_id,
                parent_session_id=parent.session_id,
                parent_turn_id=parent_turn.turn_id,
                agent_type=contract.agent_type,
                write_scope=write_scope,
                workspace_path=workspace.path if workspace is not None else None,
            )
            cancel_event = threading.Event()
            thread = threading.Thread(
                target=self._run_child,
                args=(child.session_id, contract.task, cancel_event),
                name=f"codepilot-agent-{child.session_id}",
                daemon=True,
            )
            self._running[child.session_id] = _RunningChild(handle, thread, cancel_event, workspace)
            self._emit(
                "agent_spawned",
                parent_session_id=parent.session_id,
                child_session_id=child.session_id,
                agent_type=contract.agent_type,
                parent_turn_id=parent_turn.turn_id,
            )
            thread.start()
            return {
                "agent_id": child.session_id,
                "child_session_id": child.session_id,
                "agent_type": contract.agent_type,
                "status": "queued",
            }

    def _run_child(self, child_session_id: str, task: str, cancel_event: threading.Event) -> None:
        self._set_status(child_session_id, "running")
        running = self._running[child_session_id]
        handle = running.handle
        try:
            runtime = self.child_runtime_factory(child_session_id)
            configure_profile = getattr(runtime, "configure_agent_profile", None)
            if callable(configure_profile):
                configure_profile(
                    get_agent_profile(handle.agent_type),
                    write_scope=handle.write_scope,
                )
            if cancel_event.is_set():
                self._set_terminal(child_session_id, handle, "cancelled", "cancelled before start")
                return
            submission = runtime.submit_user_message(child_session_id, task)
            if isinstance(submission, BranchConfirmationRequired):
                submission = runtime.submit_user_message(
                    child_session_id,
                    task,
                    confirmed_branch=submission.new_branch,
                )
            if isinstance(submission, BranchConfirmationRequired):
                raise RuntimeError("child branch changed during confirmation")
            self._emit(
                "agent_started",
                parent_session_id=handle.parent_session_id,
                child_session_id=child_session_id,
                agent_type=handle.agent_type,
                parent_turn_id=handle.parent_turn_id,
            )
            execution = runtime.run_turn(
                submission.turn.turn_id,
                submission.attempt.attempt_id,
                _EventCancellationToken(cancel_event),
            )
            status: AgentStatus = "cancelled" if cancel_event.is_set() or execution.result.status == "cancelled" else (
                "completed" if execution.result.status in {"success", "message_complete"} else "failed"
            )
            handle.error = execution.result.error
            status_metadata: dict[str, object] = {"result": execution.result.summary}
            changed_files: list[str] = []
            if status == "completed" and handle.workspace_path is not None:
                handle.patch_artifact_id = persist_agent_patch(
                    self.artifacts,
                    child_session_id,
                    handle.workspace_path,
                )
                changed_files = extract_paths_from_patch(self.artifacts.read_text(handle.patch_artifact_id))
                status_metadata.update(
                    {
                        "patch_artifact_id": handle.patch_artifact_id,
                        "patch_inspected_artifact_id": None,
                    }
                )
                scope_violations = [
                    path for path in changed_files if not path_allowed(path, handle.write_scope)
                ]
                if scope_violations:
                    status = "failed"
                    handle.error = (
                        "General agent changed files outside write_scope: "
                        + ", ".join(scope_violations)
                    )
                    status_metadata.update(
                        {
                            "error": handle.error,
                            "scope_violation_files": scope_violations,
                        }
                    )

            self._set_status(child_session_id, status, **status_metadata)
            if status == "completed" and handle.patch_artifact_id is not None:
                self._emit(
                    "agent_patch_ready",
                    parent_session_id=handle.parent_session_id,
                    child_session_id=child_session_id,
                    agent_type=handle.agent_type,
                    patch_artifact_id=handle.patch_artifact_id,
                    changed_files=changed_files,
                    parent_turn_id=handle.parent_turn_id,
                )
            handle.status = status
            self._emit(
                "agent_completed" if status == "completed" else "agent_cancelled" if status == "cancelled" else "agent_failed",
                parent_session_id=handle.parent_session_id,
                child_session_id=child_session_id,
                agent_type=handle.agent_type,
                patch_artifact_id=handle.patch_artifact_id,
                error=handle.error,
                parent_turn_id=handle.parent_turn_id,
            )
        except Exception as exc:
            handle.status = "failed"
            handle.error = str(exc)
            self._set_status(child_session_id, "failed", error=str(exc))
            self._emit(
                "agent_failed",
                parent_session_id=handle.parent_session_id,
                child_session_id=child_session_id,
                agent_type=handle.agent_type,
                error=str(exc),
            )
        finally:
            with self._lock:
                self._running.pop(child_session_id, None)

    def _set_terminal(self, child_session_id: str, handle: AgentHandle, status: AgentStatus, error: str | None = None) -> None:
        handle.status = status
        handle.error = error
        self._set_status(child_session_id, status, error=error)
        self._emit(
            "agent_cancelled" if status == "cancelled" else "agent_failed",
            parent_session_id=handle.parent_session_id,
            child_session_id=child_session_id,
            agent_type=handle.agent_type,
            error=error,
            parent_turn_id=handle.parent_turn_id,
        )

    def _set_status(self, session_id: str, status: AgentStatus, **extra: object) -> None:
        session = self.store.get_session(session_id)
        metadata = dict(session.metadata)
        metadata.update({"agent_status": status, **extra})
        self.store.update_session(session_id, metadata=metadata)

    def _active_children(self, parent_session_id: str) -> list[SessionRecord]:
        # A child without a local worker handle is a recovery candidate, not an
        # active worker. This keeps a restarted process from blocking new work
        # forever on a stale SQLite ``running`` turn.
        return [
            child
            for child in self.store.list_child_sessions(parent_session_id)
            if child.session_id in self._running
        ]

    def _active_writers(self, parent_session_id: str) -> list[tuple[str, ...]]:
        return [
            tuple(str(item) for item in child.metadata.get("write_scope", []))
            for child in self._active_children(parent_session_id)
            if child.metadata.get("agent_type") == "general" and child.metadata.get("write_scope")
        ]

    def _assert_owned_child(self, parent_session_id: str, child_session_id: str) -> SessionRecord:
        self._assert_primary_parent(parent_session_id)
        child = self.store.get_session(child_session_id)
        if child.parent_session_id != parent_session_id:
            raise PermissionError("child agent does not belong to parent session")
        return child

    def _assert_primary_parent(self, parent_session_id: str) -> SessionRecord:
        parent = self.store.get_session(parent_session_id)
        if parent.parent_session_id is not None or parent.metadata.get("agent_type") is not None:
            raise PermissionError("only the Primary session may control child agents")
        return parent

    def wait(self, parent_session_id: str, child_session_id: str, timeout: float | None = None) -> dict[str, object]:
        self._assert_owned_child(parent_session_id, child_session_id)
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        wait_for = self.config.wait_timeout_seconds if timeout is None else min(timeout, self.config.wait_timeout_seconds)
        with self._lock:
            running = self._running.get(child_session_id)
        if running is not None:
            running.thread.join(wait_for)
        return self.snapshot(child_session_id)

    def list_agents(self, parent_session_id: str) -> list[dict[str, object]]:
        self._assert_primary_parent(parent_session_id)
        return [self.snapshot(child.session_id) for child in self.store.list_child_sessions(parent_session_id)]

    def snapshot(self, child_session_id: str) -> dict[str, object]:
        session = self.store.get_session(child_session_id)
        turns = self.store.list_turns(child_session_id)
        latest = turns[-1] if turns else None
        recorded_status = session.metadata.get("agent_status")
        if recorded_status in {"failed", "cancelled", "completed"}:
            status = recorded_status
        elif latest is None:
            status = "running" if child_session_id in self._running else "recovery_required"
        elif latest.status == "completed":
            status = "completed"
        elif latest.status == "cancelled":
            status = "cancelled"
        elif latest.status == "recovery_required":
            status = "recovery_required"
        elif latest.status in {"running", "queued", "waiting_permission"}:
            status = "running" if child_session_id in self._running else "recovery_required"
        else:
            status = "failed"
        if status == "recovery_required" and recorded_status != status:
            self.store.update_session(
                child_session_id,
                metadata={**session.metadata, "agent_status": status},
            )
            session = self.store.get_session(child_session_id)
        artifact_id = session.metadata.get("patch_artifact_id")
        result = self._latest_completed_assistant_text(child_session_id)
        if not result:
            metadata_result = session.metadata.get("result")
            result = metadata_result if isinstance(metadata_result, str) and metadata_result.strip() else None
        changed_files: list[str] = []
        if isinstance(artifact_id, str):
            changed_files = extract_paths_from_patch(self.artifacts.read_text(artifact_id))
        return {
            "agent_id": child_session_id,
            "child_session_id": child_session_id,
            "parent_session_id": session.parent_session_id,
            "agent_type": session.metadata.get("agent_type"),
            "status": status,
            "result": result,
            "error": session.metadata.get("error"),
            "write_scope": list(session.metadata.get("write_scope", [])),
            "patch_artifact_id": artifact_id,
            "changed_files": changed_files,
            "workspace_path": session.metadata.get("workspace_path"),
            "workspace_retained": bool(session.metadata.get("workspace_path")),
        }


    def _latest_completed_assistant_text(self, child_session_id: str) -> str | None:
        """Return the latest non-empty completed assistant text for a child session.

        Assistant message rows are created with ``content=""`` and the actual model
        output is persisted in ``message_parts`` (especially for streaming responses).
        Reading only ``MessageRecord.content`` therefore turns a successful child run
        into ``result=""``. Reconstruct text from replayable text parts first and
        fall back to the legacy row content only when needed.
        """
        for message, parts in reversed(self.store.list_messages_with_parts(child_session_id)):
            if message.role != "assistant" or message.status != "completed":
                continue

            chunks: list[str] = []
            for part in parts:
                if part.type != "text" or not part.replayable:
                    continue
                value: object = part.content
                if part.artifact_id is not None:
                    try:
                        value = self.artifacts.read_text(part.artifact_id)
                    except (FileNotFoundError, LookupError, ValueError):
                        # Keep snapshot/wait usable even when an exported or damaged
                        # artifact is unavailable; the inline preview is still better
                        # than erasing an otherwise completed child result.
                        value = part.content
                if isinstance(value, str):
                    chunks.append(value)
                elif value is not None:
                    chunks.append(str(value))

            rendered = "".join(chunks).strip()
            if rendered:
                return rendered

            if isinstance(message.content, str):
                legacy = message.content.strip()
            elif message.content is None:
                legacy = ""
            else:
                legacy = str(message.content).strip()
            if legacy:
                return legacy

        return None

    def inspect_agent_patch(self, parent_session_id: str, child_session_id: str) -> dict[str, object]:
        self._assert_owned_child(parent_session_id, child_session_id)
        snapshot = self.snapshot(child_session_id)
        artifact_id = snapshot.get("patch_artifact_id")
        if not isinstance(artifact_id, str):
            raise ValueError("child agent has no patch artifact")
        patch = self.artifacts.read_text(artifact_id)
        preview = patch[:12_000]
        self.store.update_session(
            child_session_id,
            metadata={
                **self.store.get_session(child_session_id).metadata,
                "patch_inspected_artifact_id": artifact_id,
            },
        )
        return {
            "agent_id": child_session_id,
            "artifact_id": artifact_id,
            "changed_files": extract_paths_from_patch(patch),
            "patch_preview": preview,
            "patch_truncated": len(patch) > len(preview),
        }

    def apply_agent_patch(self, parent_session_id: str, child_session_id: str, parent_repo: Path) -> ToolResult:
        child = self._assert_owned_child(parent_session_id, child_session_id)
        snapshot = self.snapshot(child_session_id)
        if child.metadata.get("agent_type") != "general":
            return ToolResult(success=False, error="only general agent patches can be applied")
        if snapshot["status"] != "completed":
            return ToolResult(success=False, error="child agent is not completed")
        artifact_id = snapshot.get("patch_artifact_id")
        if not isinstance(artifact_id, str):
            return ToolResult(success=False, error="child agent has no patch artifact")
        if child.metadata.get("patch_inspected_artifact_id") != artifact_id:
            return ToolResult(success=False, error="Primary must inspect the child patch before applying it")
        if not parent_repo.exists() or not parent_repo.is_dir():
            return ToolResult(success=False, error="parent repository does not exist")
        primary_repo = self.service.open_session(parent_session_id).project_path.resolve()
        if parent_repo.resolve() != primary_repo:
            return ToolResult(success=False, error="patch must be applied to the Primary workspace")
        patch = self.artifacts.read_text(artifact_id)
        paths = extract_paths_from_patch(patch)
        if not paths:
            return ToolResult(success=False, error="child patch has no extractable paths")
        scope = tuple(str(item) for item in child.metadata.get("write_scope", []))
        if not all(path_allowed(path, scope) for path in paths):
            return ToolResult(success=False, error="child patch exceeds write_scope")
        policy = PolicyChecker.default().check(
            ToolAction(tool_name="apply_patch", arguments={"repo": parent_repo, "patch": patch}),
            context=PolicyContext(
                repo=parent_repo,
                mode="build",
                approved=True,
                metadata={"write_scope": list(scope)},
            ),
        )
        if policy.denied:
            return ToolResult(success=False, error=policy.reason, metadata=policy.metadata)
        result = apply_patch_tool(repo=parent_repo, patch=patch)
        if result.success:
            session = self.store.get_session(child_session_id)
            self.store.update_session(
                child_session_id,
                metadata={**session.metadata, "patch_applied_artifact_id": artifact_id},
            )
            self._emit(
                "agent_patch_applied",
                parent_session_id=parent_session_id,
                child_session_id=child_session_id,
                patch_artifact_id=artifact_id,
                changed_files=paths,
            )
        return result

    def close(self, parent_session_id: str, child_session_id: str, *, discard_workspace: bool = False) -> dict[str, object]:
        self._assert_owned_child(parent_session_id, child_session_id)
        with self._lock:
            running = self._running.get(child_session_id)
        if running is not None:
            running.cancel_event.set()
            running.thread.join(min(self.config.wait_timeout_seconds, 1.0))
        else:
            snapshot = self.snapshot(child_session_id)
            if snapshot["status"] in {"queued", "running", "recovery_required"}:
                child = self.store.get_session(child_session_id)
                self._set_status(child_session_id, "cancelled", error="cancelled by Primary")
                self._emit(
                    "agent_cancelled",
                    parent_session_id=parent_session_id,
                    child_session_id=child_session_id,
                    agent_type=child.metadata.get("agent_type"),
                    error="cancelled by Primary",
                    parent_turn_id=child.forked_from_turn_id,
                )
        if discard_workspace:
            session = self.store.get_session(child_session_id)
            workspace = session.metadata.get("workspace_path")
            original_repo = session.metadata.get("source_project_path")
            branch = session.metadata.get("workspace_branch")
            if isinstance(workspace, str) and isinstance(original_repo, str):
                remove_issue_worktree(workspace, original_repo=original_repo, branch_name=str(branch) if branch else None, force=True)
        return self.snapshot(child_session_id)

    def cancel_children(self, parent_session_id: str) -> list[dict[str, object]]:
        """Cancel only children controlled by the Primary session."""

        self._assert_primary_parent(parent_session_id)
        return [self.close(parent_session_id, child.session_id) for child in self.store.list_child_sessions(parent_session_id) if self.snapshot(child.session_id)["status"] in {"queued", "running", "recovery_required"}]

    def _emit(self, event_type: str, **payload: object) -> None:
        event = {"type": event_type, **payload}
        parent_session_id = payload.get("parent_session_id")
        if isinstance(parent_session_id, str):
            parent_turn_id = payload.get("parent_turn_id")
            if not isinstance(parent_turn_id, str):
                child_session_id = payload.get("child_session_id")
                if isinstance(child_session_id, str):
                    parent_turn_id = self.store.get_session(child_session_id).forked_from_turn_id
            self.store.append_event(
                session_id=parent_session_id,
                event_type=event_type,
                payload=event,
                turn_id=parent_turn_id if isinstance(parent_turn_id, str) else None,
                metadata={"source": "agent_supervisor"},
            )
        if self.event_sink is not None:
            self.event_sink(event)


class _EventCancellationToken:
    def __init__(self, event: threading.Event) -> None:
        self.event = event

    def is_cancelled(self) -> bool:
        return self.event.is_set()
