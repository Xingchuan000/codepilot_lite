from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatchFileChange:
    old_path: str | None
    new_path: str | None

    @property
    def touched_paths(self) -> list[str]:
        return [path for path in (self.old_path, self.new_path) if path is not None]


def normalize_diff_path(path: str) -> str | None:
    """把 unified diff 里的文件路径归一化成仓库内相对路径。"""

    cleaned = path.strip()
    if not cleaned:
        return None
    if cleaned.startswith("--- ") or cleaned.startswith("+++ "):
        cleaned = cleaned[4:].strip()
    cleaned = cleaned.split("\t", maxsplit=1)[0].strip()
    if not cleaned:
        return None
    cleaned = cleaned.split()[0].replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned in {"/dev/null", "dev/null"}:
        return None
    if cleaned.startswith("a/") or cleaned.startswith("b/"):
        cleaned = cleaned[2:]
    return cleaned or None


def extract_file_changes_from_patch(patch: str) -> list[PatchFileChange]:
    """从 unified diff 里提取每个文件的 old/new 路径。"""

    changes: list[PatchFileChange] = []
    pending_diff_change: PatchFileChange | None = None
    current_old: str | None = None

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            if pending_diff_change is not None:
                changes.append(pending_diff_change)
            parts = line.split()
            if len(parts) >= 4:
                pending_diff_change = PatchFileChange(
                    old_path=normalize_diff_path(parts[2]),
                    new_path=normalize_diff_path(parts[3]),
                )
            else:
                pending_diff_change = None
            current_old = None
            continue

        if line.startswith("--- "):
            current_old = normalize_diff_path(line[4:])
            continue

        if line.startswith("+++ "):
            changes.append(PatchFileChange(old_path=current_old, new_path=normalize_diff_path(line[4:])))
            pending_diff_change = None
            current_old = None

    if pending_diff_change is not None:
        changes.append(pending_diff_change)

    return changes


def extract_paths_from_patch(patch: str) -> list[str]:
    """按首次出现顺序提取 patch 中被触达的路径。"""

    paths: list[str] = []
    seen: set[str] = set()
    for change in extract_file_changes_from_patch(patch):
        for path in change.touched_paths:
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths
