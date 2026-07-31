from __future__ import annotations

import json
from typing import Any


def truncate_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, limit - len(suffix))]}{suffix}"


def safe_dict_preview(value: Any, limit: int = 800) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    preview: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if isinstance(item, dict):
            preview_item: Any = safe_dict_preview(item, max(80, limit // 4))
        elif isinstance(item, list):
            preview_item = [
                entry if isinstance(entry, (dict, list)) else str(entry)
                for entry in item[:5]
            ]
        elif isinstance(item, str):
            preview_item = truncate_text(item, max(40, limit // 4))
        else:
            preview_item = item

        candidate = {**preview, key_text: preview_item}
        if len(json.dumps(candidate, ensure_ascii=False, default=str)) > limit:
            break
        preview[key_text] = preview_item

    return preview
