from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ForkMode = Literal["none", "recent", "summary", "summary_recent", "full"]


@dataclass(frozen=True)
class ForkContextPolicy:
    mode: ForkMode = "summary_recent"
    recent_turns: int = 3

    @classmethod
    def from_session_metadata(cls, metadata: dict[str, object]) -> "ForkContextPolicy":
        raw = metadata.get("context_fork")
        if not isinstance(raw, dict):
            return cls()
        mode = str(raw.get("mode", "summary_recent"))
        recent_value = raw.get("recent_turns", 3)
        recent = recent_value if isinstance(recent_value, int) and not isinstance(recent_value, bool) else 3
        if mode not in {"none", "recent", "summary", "summary_recent", "full"}:
            mode = "summary_recent"
        return cls(mode=mode, recent_turns=max(0, min(recent, 10)))  # type: ignore[arg-type]
