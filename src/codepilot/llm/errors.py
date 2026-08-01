from __future__ import annotations

from typing import Any

_OVERFLOW_CODES = {
    "context_length_exceeded",
    "context_window_exceeded",
    "max_tokens_exceeded",
    "prompt_too_long",
    "request_too_large",
    "input_too_long",
}
_OVERFLOW_PATTERNS = (
    "maximum context length",
    "prompt is too long",
    "input is too long",
    "prompt exceeds",
    "context window exceeded",
    "too many input tokens",
    "token count exceeds",
    "request too large for context window",
)


class LLMInvocationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        output_started: bool = False,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.output_started = output_started
        self.cause = cause


class LLMContextOverflowError(LLMInvocationError):
    pass


def is_context_overflow_error(exc: BaseException) -> bool:
    if isinstance(exc, LLMContextOverflowError):
        return True
    name = type(exc).__name__.lower()
    if any(value in name for value in ("contextlength", "contextwindow", "tokenlimit")):
        return True
    code = _error_code(exc)
    if code in _OVERFLOW_CODES:
        return True
    text = str(exc).lower()
    if any(value in text for value in _OVERFLOW_PATTERNS):
        return True
    has_context_term = "context" in text or "prompt" in text or "input token" in text
    has_limit_term = "too long" in text or "exceed" in text or "maximum" in text
    return has_context_term and has_limit_term


def normalize_llm_exception(exc: BaseException, *, output_started: bool) -> LLMInvocationError:
    if isinstance(exc, LLMInvocationError):
        exc.output_started = exc.output_started or output_started
        return exc
    message = str(exc) or type(exc).__name__
    if is_context_overflow_error(exc):
        return LLMContextOverflowError(message, code=_error_code(exc), retryable=not output_started, output_started=output_started, cause=exc if isinstance(exc, Exception) else None)
    return LLMInvocationError(message, code=_error_code(exc), output_started=output_started, cause=exc if isinstance(exc, Exception) else None)


def _error_code(exc: BaseException) -> str | None:
    for value in (getattr(exc, "code", None), getattr(exc, "error_code", None)):
        if isinstance(value, str):
            return value.lower()
    response = getattr(exc, "response", None)
    data: Any = getattr(response, "json", None)
    if callable(data):
        try:
            data = data()
        except Exception:
            data = None
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"].lower()
    return None
