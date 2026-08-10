from __future__ import annotations

from dataclasses import dataclass

from codepilot.session.context_budget import estimate_tokens
from codepilot.session.model_context import ModelContextProfile

TRUNCATION_MARKER = "[PROJECT FILE TRUNCATED TO FIT CONTEXT BUDGET]"


@dataclass(frozen=True)
class InstructionBudget:
    total_tokens: int
    mandatory_tokens: int
    reference_tokens: int


def resolve_instruction_budget(
    profile: ModelContextProfile,
    *,
    ratio: float = 0.12,
    absolute_cap: int = 4_096,
) -> InstructionBudget:
    usable = max(0, profile.max_input_tokens - profile.protocol_overhead_tokens)
    total = min(absolute_cap, max(256, int(usable * ratio)))
    mandatory = int(total * 0.85)
    return InstructionBudget(total, mandatory, total - mandatory)


def truncate_text_to_tokens(text: str, max_tokens: int, *, marker: str = TRUNCATION_MARKER) -> str:
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    separator = f"\n{marker}\n"
    max_chars = max_tokens * 4
    if len(separator) >= max_chars:
        return marker[:max_chars]
    remaining = max_chars - len(separator)
    head = int(remaining * 0.7)
    return f"{text[:head]}{separator}{text[-(remaining - head):]}"
