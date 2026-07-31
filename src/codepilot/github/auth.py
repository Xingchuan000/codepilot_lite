from __future__ import annotations

import os
import re


def is_github_token_available(token_env: str = "GITHUB_TOKEN") -> bool:
    """只检查凭据是否存在，不返回敏感值。"""

    return bool(os.environ.get(token_env))


def redact_github_error(value: str, *, limit: int = 500) -> str:
    """清理 GitHub 错误消息中的 token-like 内容。"""

    redacted = re.sub(r"ghp_[A-Za-z0-9_]+", "[REDACTED]", value)
    redacted = re.sub(r"github_pat_[A-Za-z0-9_]+", "[REDACTED]", redacted)
    redacted = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"(?i)GITHUB_TOKEN", "GitHub credential", redacted)
    redacted = re.sub(r"(?i)OPENAI_API_KEY", "API credential", redacted)
    redacted = re.sub(r"(?i)ANTHROPIC_API_KEY", "API credential", redacted)
    redacted = re.sub(
        r"missing GitHub token env:\s*[A-Za-z0-9_]+",
        "missing required GitHub credential",
        redacted,
    )
    return redacted[:limit]
