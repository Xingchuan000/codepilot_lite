from __future__ import annotations

"""从第十四步 CI feedback manifest 中提取 actionable fingerprints，并计算轮次差分。"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from codepilot.post_pr.models import FeedbackDelta


_ACTIONABLE_SEVERITIES = {"blocking", "error"}
_NON_ACTIONABLE_KINDS = {"pending_check"}
_NON_ACTIONABLE_STATUSES = {"non_actionable", "note", "resolved", "dismissed", "closed", "ignored"}
_NON_ACTIONABLE_CONCLUSIONS = {"success", "neutral"}
_VOLATILE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b"),
    re.compile(r"\b(run|job|workflow)[-_ ]?id[:= ]+\d+\b", re.IGNORECASE),
    re.compile(r"\b\d{8,}\b"),
]


def load_ci_feedback_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid ci_feedback_manifest JSON: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise ValueError("ci_feedback_manifest must be a JSON object")
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.startswith("codepilot.ci_feedback_manifest"):
        raise ValueError("unsupported ci_feedback_manifest schema_version")
    return data


def fallback_feedback_fingerprint(item: dict[str, Any]) -> str:
    summary = str(item.get("summary") or "")
    normalized = summary.lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for pattern in _VOLATILE_PATTERNS:
        normalized = pattern.sub("", normalized)
    payload = "|".join(
        [
            str(item.get("kind") or ""),
            str(item.get("source") or ""),
            str(item.get("check_name") or ""),
            str(item.get("file_path") or ""),
            normalized,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def extract_feedback_fingerprints(ci_feedback_manifest: dict[str, Any]) -> list[str]:
    fingerprints: set[str] = set()
    items = (ci_feedback_manifest.get("safe_summary") or {}).get("feedback_items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("severity") not in _ACTIONABLE_SEVERITIES:
            continue
        if item.get("kind") in _NON_ACTIONABLE_KINDS:
            continue
        if item.get("status") in _NON_ACTIONABLE_STATUSES:
            continue
        if item.get("conclusion") in _NON_ACTIONABLE_CONCLUSIONS:
            continue
        if item.get("stale") is True:
            continue
        if item.get("confidence") == "low":
            continue
        fingerprint = item.get("fingerprint")
        fingerprints.add(str(fingerprint) if isinstance(fingerprint, str) and fingerprint else fallback_feedback_fingerprint(item))
    return sorted(fingerprints)


def build_feedback_delta(*, previous_fingerprints: list[str], current_fingerprints: list[str]) -> FeedbackDelta:
    previous = set(previous_fingerprints)
    current = set(current_fingerprints)
    new = sorted(current - previous)
    repeated = sorted(current & previous)
    resolved = sorted(previous - current)
    return FeedbackDelta(
        current_fingerprints=sorted(current),
        previous_fingerprints=sorted(previous),
        new_fingerprints=new,
        repeated_fingerprints=repeated,
        resolved_fingerprints=resolved,
        is_repeated_failure=bool(current) and current == previous,
        progressed=bool(resolved) and not new,
        regressed=bool(new) and bool(previous),
    )


def should_stop_for_repeated_feedback(delta: FeedbackDelta, *, stop_on_repeated_feedback: bool) -> bool:
    return stop_on_repeated_feedback and delta.is_repeated_failure


def classify_check_terminal_reason(ci_feedback_manifest: dict[str, Any]) -> str | None:
    safe_summary = ci_feedback_manifest.get("safe_summary") or {}
    checks = safe_summary.get("checks") or []
    if not isinstance(checks, list):
        return None
    conclusions = [str(item.get("conclusion") or "") for item in checks if isinstance(item, dict)]
    summary = ci_feedback_manifest.get("summary")
    total = summary.get("checks_total") if isinstance(summary, dict) else None
    status = ci_feedback_manifest.get("status")
    if "pending" in conclusions:
        return "pending_checks"
    if "timed_out" in conclusions:
        return "ci_timeout"
    if "cancelled" in conclusions:
        return "checks_cancelled"
    if "skipped" in conclusions and not extract_feedback_fingerprints(ci_feedback_manifest):
        return "checks_skipped"
    if total == 0 and status in {"api_degraded", "feedback_unavailable"}:
        return "checks_unavailable"
    return None
