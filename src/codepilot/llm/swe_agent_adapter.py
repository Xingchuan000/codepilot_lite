from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from codepilot.llm.types import ChatMessage, LLMResponse, RichChatMessage
from codepilot.session.provider_messages import to_provider_messages


def _preview_text(text: str, max_chars: int) -> str:
    """截断长文本，避免把完整大响应塞进 raw。"""

    if len(text) <= max_chars:
        return text
    suffix = "... truncated"
    return f"{text[: max(0, max_chars - len(suffix))]}{suffix}"


def _looks_like_safe_text_model(model: Any) -> bool:
    """只允许测试模型走 query 回退路径，避免真实模型误走 bash-only query。"""

    return model.__class__.__module__.startswith("minisweagent.models.test_models")


def _safe_json_value(value: Any, max_chars: int) -> Any:
    """把 raw 响应压缩成可 JSON 序列化、可审计的最小结构。"""

    if isinstance(value, dict):
        return {str(key): _safe_json_value(item, max_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json_value(item, max_chars) for item in value]
    if isinstance(value, tuple):
        return [_safe_json_value(item, max_chars) for item in value]
    if isinstance(value, str):
        return _preview_text(value, max_chars)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _preview_text(repr(value), max_chars)


def safe_raw_response(raw: Any, max_chars: int = 4000) -> dict[str, Any]:
    """把 mini-SWE-agent 返回值整理成安全且短小的 raw 摘要。

    这里不保留完整 provider response，只保留 loop 和测试真正需要的关键信息。
    """

    if isinstance(raw, dict):
        result: dict[str, Any] = {}
        for key in ("role", "content", "model", "usage"):
            if key in raw:
                result[key] = _safe_json_value(raw[key], max_chars)
        extra = raw.get("extra")
        if isinstance(extra, dict):
            extra_result: dict[str, Any] = {}
            if "cost" in extra:
                extra_result["cost"] = _safe_json_value(extra["cost"], max_chars)
            if "timestamp" in extra:
                extra_result["timestamp"] = _safe_json_value(extra["timestamp"], max_chars)
            if "usage" in extra:
                extra_result["usage"] = _safe_json_value(extra["usage"], max_chars)
            if "response" in extra:
                extra_result["response_preview"] = _preview_text(json.dumps(_safe_json_value(extra["response"], max_chars), ensure_ascii=False), max_chars)
            if extra_result:
                result["extra"] = extra_result
        return result
    if hasattr(raw, "model_dump"):
        try:
            return safe_raw_response(raw.model_dump(mode="json"), max_chars=max_chars)
        except Exception:
            pass
    return {"repr": _preview_text(repr(raw), max_chars)}


def _extract_content(raw: Any) -> str:
    """从多种 mini-SWE-agent 风格返回值里提取 assistant 文本。"""

    if isinstance(raw, dict):
        if isinstance(raw.get("content"), str):
            return raw["content"]
        message = raw.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        return ""
    choices = getattr(raw, "choices", None)
    if choices and getattr(choices[0], "message", None) is not None:
        content = getattr(choices[0].message, "content", None)
        if isinstance(content, str):
            return content
    return ""


@dataclass
class SweAgentModelAdapter:
    """把 mini-SWE-agent 模型对象包装成 CodePilotLLMClient。"""

    model: Any

    def complete(self, messages: list[ChatMessage | RichChatMessage]) -> LLMResponse:
        """只取纯文本响应，不允许走工具调用解析或执行路径。"""

        provider_messages = to_provider_messages(messages)
        if hasattr(self.model, "query_without_default_tools"):
            raw = self.model.query_without_default_tools(provider_messages)
        else:
            if not _looks_like_safe_text_model(self.model):
                raise RuntimeError(
                    "Model does not support query_without_default_tools; refusing to use bash-only query path"
                )
            raw = self.model.query(provider_messages)
        usage: dict[str, Any] = {}
        model_name: str | None = None
        if isinstance(raw, dict):
            extra = raw.get("extra")
            if isinstance(extra, dict) and isinstance(extra.get("usage"), dict):
                usage = extra["usage"]
            if isinstance(raw.get("model"), str):
                model_name = raw["model"]
        return LLMResponse(
            content=_extract_content(raw),
            raw=safe_raw_response(raw),
            model=model_name or getattr(getattr(self.model, "config", None), "model_name", None),
            usage=usage,
        )
