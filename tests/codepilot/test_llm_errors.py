from codepilot.llm.errors import (
    LLMContextOverflowError,
    LLMInvocationError,
    is_context_overflow_error,
    normalize_llm_exception,
)


class _ProviderError(RuntimeError):
    code = "context_length_exceeded"


def test_context_overflow_classification_excludes_unrelated_failures() -> None:
    assert is_context_overflow_error(_ProviderError("request rejected"))
    assert is_context_overflow_error(RuntimeError("maximum context length exceeded"))
    assert not is_context_overflow_error(RuntimeError("rate limit exceeded"))
    assert not is_context_overflow_error(TimeoutError("timed out"))
    assert is_context_overflow_error(RuntimeError("prompt is too long: 201000 tokens > 200000 maximum"))
    assert is_context_overflow_error(RuntimeError("request too large for context window"))
    assert not is_context_overflow_error(RuntimeError("uploaded file is too large"))
    assert not is_context_overflow_error(RuntimeError("output token limit reached after successful generation"))
    assert not is_context_overflow_error(RuntimeError("authentication token expired"))


def test_normalize_preserves_partial_output_state() -> None:
    assert isinstance(normalize_llm_exception(_ProviderError("too large"), output_started=False), LLMContextOverflowError)
    normalized = normalize_llm_exception(TimeoutError("timed out"), output_started=True)
    assert isinstance(normalized, LLMInvocationError)
    assert normalized.output_started is True
