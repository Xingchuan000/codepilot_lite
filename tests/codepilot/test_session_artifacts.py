from __future__ import annotations

import hashlib
from pathlib import Path

from codepilot.session.artifacts import ArtifactStore
from codepilot.session.database import SessionDatabase
from codepilot.session.paths import resolve_session_paths
from codepilot.session.store import SessionStore


def _artifact_store(tmp_path: Path) -> tuple[ArtifactStore, SessionStore]:
    paths = resolve_session_paths(tmp_path)
    database = SessionDatabase(paths.database_path)
    database.initialize()
    return ArtifactStore(database, paths), SessionStore(database, paths)


def test_small_text_stays_inline_and_can_be_read(tmp_path: Path) -> None:
    artifacts, store = _artifact_store(tmp_path)
    session = store.create_session(
        project_path=tmp_path / "repo",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )

    artifact = artifacts.put_text(session.session_id, "note", "hello")

    assert artifact.storage_path == "inline"
    assert artifacts.read_text(artifact.artifact_id) == "hello"
    assert list((resolve_session_paths(tmp_path).exports_dir).glob("*")) == []


def test_small_bytes_stay_inline_and_can_be_read(tmp_path: Path) -> None:
    artifacts, store = _artifact_store(tmp_path)
    session = store.create_session(
        project_path=tmp_path / "repo",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )

    artifact = artifacts.put_bytes(session.session_id, "blob", b"hello")

    assert artifact.storage_path == "inline"
    assert artifacts.read_text(artifact.artifact_id) == "hello"


def test_large_text_writes_file_and_preserves_hash(tmp_path: Path) -> None:
    artifacts, store = _artifact_store(tmp_path)
    session = store.create_session(
        project_path=tmp_path / "repo",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )
    content = "x" * 20_000

    artifact = artifacts.put_text(session.session_id, "log", content)

    assert artifact.storage_path != "inline"
    assert Path(artifact.storage_path).read_text(encoding="utf-8") == content
    assert artifact.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert artifacts.read_text(artifact.artifact_id) == content


def test_session_directories_are_isolated_and_archive_keeps_artifacts(tmp_path: Path) -> None:
    artifacts, store = _artifact_store(tmp_path)
    first = store.create_session(
        project_path=tmp_path / "repo-a",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )
    second = store.create_session(
        project_path=tmp_path / "repo-b",
        provider="openai",
        current_model="gpt-4.1",
        permission_mode="manual",
    )
    first_artifact = artifacts.put_text(first.session_id, "note", "a" * 20_000)
    second_artifact = artifacts.put_text(second.session_id, "note", "b" * 20_000)

    assert Path(first_artifact.storage_path).parent.parent.name == first.session_id
    assert Path(second_artifact.storage_path).parent.parent.name == second.session_id
    store.archive_session(first.session_id)
    assert Path(first_artifact.storage_path).exists()
