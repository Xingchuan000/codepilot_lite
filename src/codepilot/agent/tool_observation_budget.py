from __future__ import annotations

from dataclasses import dataclass

from codepilot.session.model_capabilities import ModelContextProfile


@dataclass(frozen=True)
class ToolObservationBudget:
    token_limit: int
    strategy: str


class ToolObservationBudgetPolicy:
    CODE_TOOLS = {"read_file", "search_code", "git_diff"}
    WRITE_TOOLS = {"apply_patch", "replace_range", "write_file"}
    NOISE_TOOLS = {"run_tests", "run_shell", "list_files"}

    def resolve(
        self,
        *,
        tool_name: str,
        profile: ModelContextProfile,
        estimated_result_tokens: int,
    ) -> ToolObservationBudget:
        if tool_name in self.CODE_TOOLS:
            limit = max(768, min(4096, int(profile.max_input_tokens * 0.08)))
            strategy = "code"
        elif tool_name in self.WRITE_TOOLS:
            limit = max(256, min(512, int(profile.max_input_tokens * 0.02)))
            strategy = "write"
        else:
            limit = max(256, min(2000, int(profile.max_input_tokens * 0.03)))
            strategy = "noise" if tool_name in self.NOISE_TOOLS else "fallback"
        return ToolObservationBudget(limit, strategy)
