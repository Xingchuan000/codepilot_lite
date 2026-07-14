from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codepilot.session.database import SessionDatabase
from codepilot.session.ids import make_artifact_id
from codepilot.session.models import ArtifactRecord, to_jsonable
from codepilot.session.paths import SessionPaths, resolve_session_paths
from codepilot.session.store import SessionStore


INLINE_CONTENT_MAX_CHARS = 16_000
PREVIEW_CONTENT_MAX_CHARS = 1_000


def _preview_text(content: str) -> str:
    if len(content) <= PREVIEW_CONTENT_MAX_CHARS:
        return content
    suffix = "... truncated"
    return f"{content[: max(0, PREVIEW_CONTENT_MAX_CHARS - len(suffix))]}{suffix}"


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(to_jsonable(content), ensure_ascii=False, separators=(",", ":"))


def _content_mime_type(content: Any) -> str:
    return "text/plain" if isinstance(content, str) else "application/json"


@dataclass(frozen=True)
class PersistedContent:
    inline_content: Any | None
    preview: str
    artifact_id: str | None


class ArtifactStore:
    """管理 Session 内部 artifact 文件和数据库索引。

    这层只负责落盘、读回和导出，不做自动清理，也不参与任何 TUI 逻辑。
    """

    def __init__(self, database: SessionDatabase, paths: SessionPaths | None = None) -> None:
        self.database = database
        self.paths = paths or resolve_session_paths(database.path.parent)
        self.store = SessionStore(database, self.paths)

    def persist_content(self, session_id: str, kind: str, content: Any) -> PersistedContent:
        # 先把内容规整成稳定文本，再按长度决定直接内联还是落盘成 artifact。
        text = _stringify_content(content)
        if len(text) <= INLINE_CONTENT_MAX_CHARS:
            return PersistedContent(content, text, None)
        artifact = self.put_text(session_id, kind, text, mime_type=_content_mime_type(content))
        return PersistedContent(None, _preview_text(text), artifact.artifact_id)

    def put_text(self, session_id: str, kind: str, content: str, mime_type: str = "text/plain") -> ArtifactRecord:
        if len(content) <= INLINE_CONTENT_MAX_CHARS:
            return self.store.create_artifact(
                session_id=session_id,
                kind=kind,
                mime_type=mime_type,
                size_bytes=len(content.encode("utf-8")),
                sha256=_sha256_text(content),
                storage_path="inline",
                content=content,
            )
        return self._put_external(session_id, kind, content.encode("utf-8"), mime_type=mime_type)

    def put_bytes(self, session_id: str, kind: str, content: bytes, mime_type: str = "application/octet-stream") -> ArtifactRecord:
        if len(content) <= INLINE_CONTENT_MAX_CHARS:
            return self.store.create_artifact(
                session_id=session_id,
                kind=kind,
                mime_type=mime_type,
                size_bytes=len(content),
                sha256=_sha256_bytes(content),
                storage_path="inline",
                content={"encoding": "base64", "data": base64.b64encode(content).decode("ascii")},
            )
        return self._put_external(session_id, kind, content, mime_type=mime_type)

    def read_text(self, artifact_id: str) -> str:
        return self.read_bytes(artifact_id).decode("utf-8")

    def read_bytes(self, artifact_id: str) -> bytes:
        artifact = self._get_artifact(artifact_id)
        if artifact.storage_path == "inline":
            if isinstance(artifact.content, str):
                content = artifact.content.encode("utf-8")
            elif isinstance(artifact.content, dict) and artifact.content.get("encoding") == "base64":
                content = base64.b64decode(str(artifact.content["data"]))
            else:
                raise ValueError(f"Artifact {artifact_id} has unsupported inline content")
        else:
            path = Path(artifact.storage_path)
            if not path.exists():
                raise FileNotFoundError(f"Artifact file missing: {path}")
            content = path.read_bytes()
        if len(content) != artifact.size_bytes:
            raise ValueError(f"Artifact size mismatch: {artifact_id}")
        if _sha256_bytes(content) != artifact.sha256:
            raise ValueError(f"Artifact hash mismatch: {artifact_id}")
        return content

    def copy_to_export(self, artifact_id: str, target_dir: Path) -> Path:
        artifact = self._get_artifact(artifact_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".txt" if artifact.mime_type.startswith("text/") else ".bin"
        target_path = target_dir / f"{artifact.artifact_id}{suffix}"
        target_path.write_bytes(self.read_bytes(artifact_id))
        return target_path

    def _put_external(self, session_id: str, kind: str, content: bytes, *, mime_type: str) -> ArtifactRecord:
        artifact_id = make_artifact_id()
        artifact_dir = self.paths.sessions_dir / session_id / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        final_path = artifact_dir / artifact_id
        with tempfile.NamedTemporaryFile(prefix=f"{artifact_id}.", suffix=".tmp", dir=artifact_dir, delete=False) as file:
            tmp_path = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        try:
            tmp_path.replace(final_path)
            dir_fd = os.open(artifact_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            record = self.store.create_artifact(
                session_id=session_id,
                kind=kind,
                mime_type=mime_type,
                size_bytes=len(content),
                sha256=_sha256_bytes(content),
                storage_path=str(final_path),
                artifact_id=artifact_id,
            )
        except Exception:
            final_path.unlink(missing_ok=True)
            tmp_path.unlink(missing_ok=True)
            raise
        return record

    def _get_artifact(self, artifact_id: str) -> ArtifactRecord:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise LookupError(artifact_id)
        return self.store._artifact_from_row(row)


def _sha256_text(content: str) -> str:
    return _sha256_bytes(content.encode("utf-8"))


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
