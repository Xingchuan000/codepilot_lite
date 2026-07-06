from __future__ import annotations

"""读取并校验第十一步生成的 artifact manifest。"""

import json
import re
from pathlib import Path
from typing import Any

from codepilot.pr_assist.models import ManifestInvalidError, PRAssistInput, PRAssistSafetyGate
from codepilot.repo.git_utils import sha256_file


TOKEN_LIKE_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)OPENAI_API_KEY"),
    re.compile(r"(?i)ANTHROPIC_API_KEY"),
    re.compile(r"(?i)GITHUB_TOKEN"),
]


def load_artifact_manifest(path: str | Path) -> dict[str, Any]:
    """读取 artifact_manifest.json，并把明显格式错误转成专用异常。"""

    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    if not manifest_path.is_file():
        raise ManifestInvalidError(f"artifact_manifest path is not a file: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestInvalidError(f"artifact_manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise ManifestInvalidError("artifact_manifest must be a JSON object")
    return data


def scan_token_like_strings(value: Any) -> list[str]:
    """把任意结构序列化成文本后做轻量 token 模式扫描。"""

    text = json.dumps(value, ensure_ascii=False, default=str)
    matches: list[str] = []
    for pattern in TOKEN_LIKE_PATTERNS:
        if pattern.search(text):
            matches.append(pattern.pattern)
    return matches


def validate_artifact_manifest(manifest: dict[str, Any]) -> list[str]:
    """检查 manifest 顶层字段是否满足第十二步最小契约。"""

    errors: list[str] = []
    if manifest.get("schema_version") != "codepilot.artifact_manifest.v1":
        errors.append("unsupported schema_version")
    if not manifest.get("run_id"):
        errors.append("missing run_id")
    if "safety_decision" not in manifest:
        errors.append("missing safety_decision")
    if "safety_summary" not in manifest:
        errors.append("missing safety_summary")
    if "artifacts" not in manifest or not isinstance(manifest.get("artifacts"), list):
        errors.append("missing artifacts list")
    if "patch" not in manifest:
        errors.append("missing patch metadata field")
    if scan_token_like_strings(manifest):
        errors.append("token-like string detected in artifact_manifest")
    return errors


def artifact_entries_by_name(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 artifacts 列表按 name 转成索引，方便后续按固定名字取值。"""

    entries = manifest.get("artifacts") or []
    result: dict[str, dict[str, Any]] = {}
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result[item["name"]] = item
    return result


def resolve_artifact_path(run_dir: str | Path, entry: dict[str, Any]) -> Path:
    """只允许相对 run_dir 的路径，避免通过 manifest 逃逸到别处。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ManifestInvalidError("artifact entry path must be non-empty string")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ManifestInvalidError(f"artifact path must be relative to run_dir: {raw_path}")
    resolved = (run_dir_path / candidate).resolve()
    try:
        resolved.relative_to(run_dir_path)
    except ValueError as exc:
        raise ManifestInvalidError(f"artifact path escapes run_dir: {raw_path}") from exc
    return resolved


def verify_artifact_integrity(path: Path, entry: dict[str, Any]) -> list[str]:
    """校验 exists / size / sha256 是否和磁盘上的真实文件一致。"""

    errors: list[str] = []
    if bool(entry.get("exists")) and not path.exists():
        errors.append(f"artifact missing on disk: {entry.get('name')}")
        return errors
    if not path.exists():
        return errors
    expected_size = entry.get("size_bytes")
    if isinstance(expected_size, int) and path.stat().st_size != expected_size:
        errors.append(f"artifact size mismatch: {entry.get('name')}")
    expected_sha = entry.get("sha256")
    if isinstance(expected_sha, str) and expected_sha:
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha:
            errors.append(f"artifact sha256 mismatch: {entry.get('name')}")
    return errors


def verify_manifest_self_artifact(path: Path, entry: dict[str, Any]) -> list[str]:
    """artifact_manifest 自身只检查路径存在，避免二阶段回填造成 size 漂移误判。"""

    errors: list[str] = []
    if bool(entry.get("exists")) and not path.exists():
        errors.append("artifact missing on disk: artifact_manifest")
        return errors
    if not path.exists():
        errors.append("artifact missing on disk: artifact_manifest")
    return errors


def resolve_required_artifacts(run_dir: str | Path, manifest: dict[str, Any]) -> dict[str, Path]:
    """把 manifest 中登记的 artifact 解析成真实绝对路径。"""

    entries = artifact_entries_by_name(manifest)
    return {name: resolve_artifact_path(run_dir, entry) for name, entry in entries.items()}


def validate_required_artifacts(run_dir: str | Path, manifest: dict[str, Any]) -> list[str]:
    """按 safety 状态检查第十二步真正依赖的那些基础产物。"""

    errors = validate_artifact_manifest(manifest)
    entries = artifact_entries_by_name(manifest)

    always_required = ["issue_json", "pr_summary", "restore_plan", "artifact_manifest"]
    for name in always_required:
        if name not in entries:
            errors.append(f"missing artifact entry: {name}")
            continue
        path = resolve_artifact_path(run_dir, entries[name])
        if name == "artifact_manifest":
            errors.extend(verify_manifest_self_artifact(path, entries[name]))
        else:
            errors.extend(verify_artifact_integrity(path, entries[name]))

    safety_decision = manifest.get("safety_decision")
    status = manifest.get("status")
    safety_failed = safety_decision == "deny" or status in {
        "repo_safety_denied",
        "protected_patch_path_denied",
        "protected_after_path_denied",
    }

    if not safety_failed:
        if "patch" not in entries:
            errors.append("missing artifact entry: patch")
        else:
            errors.extend(verify_artifact_integrity(resolve_artifact_path(run_dir, entries["patch"]), entries["patch"]))
        has_report_json = "report_json" in entries
        has_report_md = "report_md" in entries
        if not has_report_json and not has_report_md:
            errors.append("missing report artifact entry: report_json or report_md")
        else:
            for name in ["report_json", "report_md"]:
                if name in entries:
                    errors.extend(
                        verify_artifact_integrity(resolve_artifact_path(run_dir, entries[name]), entries[name])
                    )
        if manifest.get("patch") is None:
            errors.append("missing patch metadata for safety-passed run")
    elif "patch" in entries:
        path = resolve_artifact_path(run_dir, entries["patch"])
        errors.extend(verify_artifact_integrity(path, entries["patch"]))

    return errors


def build_safety_gate(manifest: dict[str, Any]) -> PRAssistSafetyGate:
    """把第十一步的安全摘要投影成 pass / warn / fail。"""

    reasons: list[str] = []
    warnings = [str(item) for item in manifest.get("safety_warnings") or []]
    safety_decision = manifest.get("safety_decision")
    status = manifest.get("status")
    patch = manifest.get("patch") or {}
    safety_summary = manifest.get("safety_summary") or {}

    if manifest.get("safety_reason"):
        reasons.append(str(manifest["safety_reason"]))
    if status in {"repo_safety_denied", "protected_patch_path_denied", "protected_after_path_denied"}:
        reasons.append(str(status))
    if patch.get("protected_changed_files"):
        reasons.append("protected_changed_files present")
    if safety_summary.get("protected_after_files"):
        reasons.append("protected_after_files present")

    if safety_decision == "deny" or reasons:
        return PRAssistSafetyGate(status="fail", reasons=reasons, warnings=warnings)
    if safety_decision == "warn" or manifest.get("success") is False or safety_summary.get("baseline_dirty") is True:
        return PRAssistSafetyGate(status="warn", reasons=reasons, warnings=warnings)
    return PRAssistSafetyGate(status="pass", reasons=reasons, warnings=warnings)


def build_pr_assist_input(
    run_dir: str | Path,
    *,
    redact_absolute_paths: bool = True,
    strict_safety: bool = True,
) -> PRAssistInput:
    """根据 run_dir 构造 workflow 入口对象。"""

    run_dir_path = Path(run_dir).expanduser().resolve()
    manifest_path = run_dir_path / "artifact_manifest.json"
    manifest = load_artifact_manifest(manifest_path)
    run_id = str(manifest.get("run_id") or run_dir_path.name)
    return PRAssistInput(
        run_id=run_id,
        run_dir=run_dir_path,
        manifest_path=manifest_path,
        redact_absolute_paths=redact_absolute_paths,
        strict_safety=strict_safety,
    )
