from __future__ import annotations

import json
import re
from typing import Any


TOKEN_LIKE_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)OPENAI_API_KEY"),
    re.compile(r"(?i)ANTHROPIC_API_KEY"),
    re.compile(r"(?i)GITHUB_TOKEN"),
]


def scan_token_like_strings(value: Any) -> list[str]:
    """把任意结构序列化成文本后做轻量 token 模式扫描。"""

    text = json.dumps(value, ensure_ascii=False, default=str)
    matches: list[str] = []
    for pattern in TOKEN_LIKE_PATTERNS:
        if pattern.search(text):
            matches.append(pattern.pattern)
    return matches
