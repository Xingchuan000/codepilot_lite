from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from codepilot.tools.base import ToolSpec

_PROVIDER_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_PROVIDER_TOOL_NAME_MAX_LENGTH = 64


def to_provider_tool_name(name: str) -> str:
    """Return a provider-safe alias while keeping CodePilot's internal name untouched.

    CodePilot deliberately allows richer internal names such as
    ``mcp.research_lab.fetch_url``. Several native function-calling providers only
    accept ``[A-Za-z0-9_-]`` names, so the provider boundary needs a deterministic
    alias that can be mapped back after the response arrives.
    """

    if _PROVIDER_TOOL_NAME_RE.fullmatch(name) and len(name) <= _PROVIDER_TOOL_NAME_MAX_LENGTH:
        return name

    readable = re.sub(r"[^a-zA-Z0-9_-]+", "__", name).strip("_") or "tool"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    suffix = f"__{digest}"
    prefix_limit = _PROVIDER_TOOL_NAME_MAX_LENGTH - len(suffix)
    readable = readable[:prefix_limit].rstrip("_") or "tool"
    return f"{readable}{suffix}"


def build_provider_tool_name_map(specs: Sequence[ToolSpec]) -> dict[str, str]:
    """Build the per-request CodePilot-name -> provider-name mapping."""

    mapping = {spec.name: to_provider_tool_name(spec.name) for spec in specs}
    reverse: dict[str, str] = {}
    for codepilot_name, provider_name in mapping.items():
        previous = reverse.get(provider_name)
        if previous is not None and previous != codepilot_name:
            raise ValueError(
                "Provider tool-name alias collision: "
                f"{previous!r} and {codepilot_name!r} -> {provider_name!r}"
            )
        reverse[provider_name] = codepilot_name
    return mapping


def to_litellm_tools(
    specs: Sequence[ToolSpec],
    *,
    tool_name_map: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    names = tool_name_map if tool_name_map is not None else build_provider_tool_name_map(specs)
    return [
        {
            "type": "function",
            "function": {
                "name": names[spec.name],
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }
        for spec in specs
    ]
