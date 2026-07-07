from __future__ import annotations

import re
from pathlib import Path

ABSOLUTE_PATH_IN_TEXT_PATTERN = re.compile(r"(?<![A-Za-z0-9+.-])/(?:[^\s'\"`<>]|\\.)+")
URL_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+")

SENSITIVE_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "set-cookie",
    "client_secret",
    "private_key",
    "env",
    "environment",
}

SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "private_key",
    "client_secret",
)

SENSITIVE_PATTERNS = (
    re.compile(r"(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[a-z0-9._\-]+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]{16,}"),
    re.compile(r"(?i)(token|api[_-]?key|password|secret|client_secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
)


def truncate_text(text: str, *, max_chars: int = 500) -> str:
    if max_chars < 20:
        max_chars = 20
    if len(text) <= max_chars:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}"


def redact_string(text: str, *, max_string_chars: int = 500) -> str:
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return truncate_text(redacted, max_chars=max_string_chars)


def redact_value(value: object, *, max_string_chars: int = 500) -> object:
    if isinstance(value, dict):
        output: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in SENSITIVE_KEYS or any(part in lowered for part in SENSITIVE_KEY_PARTS):
                output[key_text] = "[REDACTED]"
            else:
                output[key_text] = redact_value(item, max_string_chars=max_string_chars)
        return output
    if isinstance(value, list):
        return [redact_value(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, max_string_chars=max_string_chars) for item in value)
    if isinstance(value, set):
        return sorted(redact_value(item, max_string_chars=max_string_chars) for item in value)
    if isinstance(value, Path):
        return redact_string(str(value), max_string_chars=max_string_chars)
    if isinstance(value, str):
        return redact_string(value, max_string_chars=max_string_chars)
    return value


def relative_paths_in_text(text: str, *, base_dir: str | Path | None = None) -> str:
    if not text:
        return text

    url_spans = [(match.start(), match.end()) for match in URL_PATTERN.finditer(text)]
    trailing_punctuation = ".,:;!?)]}"

    def is_url_span(start: int, end: int) -> bool:
        return any(span_start <= start and end <= span_end for span_start, span_end in url_spans)

    pieces: list[str] = []
    last_index = 0
    for match in ABSOLUTE_PATH_IN_TEXT_PATTERN.finditer(text):
        start, end = match.span()
        if is_url_span(start, end):
            continue
        pieces.append(text[last_index:start])
        path_text = match.group(0)
        suffix = ""
        while path_text and path_text[-1] in trailing_punctuation:
            suffix = path_text[-1] + suffix
            path_text = path_text[:-1]
        if not path_text:
            pieces.append(match.group(0))
            last_index = end
            continue
        try:
            pieces.append(relative_path_for_display(path_text, base_dir=base_dir))
        except Exception:
            pieces.append(Path(path_text).name)
        pieces.append(suffix)
        last_index = end
    pieces.append(text[last_index:])
    return "".join(pieces)


def relative_path_for_display(path: str | Path, *, base_dir: str | Path | None = None) -> str:
    path_obj = Path(path)
    if base_dir is None:
        return path_obj.name if path_obj.is_absolute() else str(path_obj)
    base = Path(base_dir)
    try:
        return str(path_obj.resolve().relative_to(base.resolve()))
    except Exception:
        return path_obj.name if path_obj.is_absolute() else str(path_obj)
