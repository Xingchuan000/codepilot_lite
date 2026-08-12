from __future__ import annotations

import fnmatch
import re
import shlex
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any, cast

from codepilot.common.patches import extract_paths_from_patch
from codepilot.policy.config import PolicyConfig
from codepilot.policy.defaults import default_policy_config
from codepilot.policy.effects import ActionEffect, ActionEffectClassifier
from codepilot.policy.models import PolicyContext, PolicyDecision, PolicyDecisionValue
from codepilot.tools.actions import ToolAction
from codepilot.tools.base import ExternalImpact, Reversibility, ToolSpec
from codepilot.tools.registry import find_tool_spec

STRUCTURED_WRITE_TOOLS = {"apply_patch", "replace_range"}
PROTECTED_COMMAND_TOKENS = [".env", ".github/workflows", ".codepilot", "secrets/", ".ssh"]


class PolicyChecker:
    """用硬边界和动作事实判断工具动作是否可以执行。"""

    def __init__(
        self,
        config: PolicyConfig | None = None,
        extra_tool_specs: Mapping[str, ToolSpec] | None = None,
    ) -> None:
        self.config = config or default_policy_config()
        self.extra_tool_specs = dict(extra_tool_specs or {})
        self.effect_classifier = ActionEffectClassifier()

    @classmethod
    def default(cls, extra_tool_specs: Mapping[str, ToolSpec] | None = None) -> PolicyChecker:
        return cls(default_policy_config(), extra_tool_specs=extra_tool_specs)

    def _find_tool_spec(self, name: str) -> ToolSpec | None:
        return self.extra_tool_specs.get(name) or find_tool_spec(name)

    def check(
        self,
        action: ToolAction,
        context: PolicyContext | None = None,
        *,
        spec: ToolSpec | None = None,
    ) -> PolicyDecision:
        context = context or PolicyContext()
        tool_name = action.tool_name
        arguments = dict(action.arguments or {})

        profile_decision = self._check_agent_boundary(tool_name=tool_name, context=context)
        if profile_decision is not None:
            return profile_decision

        spec = spec or self._find_tool_spec(tool_name)
        if spec is None:
            return self._decision(
                "allow",
                "Unknown tool is allowed to reach the registry so it can return a structured unknown-tool error.",
                tool_name=tool_name,
                matched_rule="tool.unknown.allow_to_registry",
                context=context,
                metadata={"known_tool": False},
            )

        metadata = self._base_metadata(spec, context)
        repo_root = self._resolve_repo_root(arguments=arguments, context=context, metadata=metadata)

        path_decision = self._check_paths(
            tool_name=tool_name,
            arguments=arguments,
            repo_root=repo_root,
            context=context,
            metadata=metadata,
        )
        if path_decision is not None:
            return path_decision

        hard_deny = self._check_hard_deny_command(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            metadata=metadata,
        )
        if hard_deny is not None:
            return hard_deny

        effect = self.effect_classifier.classify(tool_name=tool_name, arguments=arguments, spec=spec)
        effect_metadata = {
            **metadata,
            "external_impact": effect.external_impact.value,
            "reversibility": effect.reversibility.value,
            "effect_reason": effect.reason,
        }
        return self._decide_effect(
            tool_name=tool_name,
            effect=effect,
            context=context,
            metadata=effect_metadata,
        )

    def _check_agent_boundary(self, *, tool_name: str, context: PolicyContext) -> PolicyDecision | None:
        allowed_tools = context.metadata.get("allowed_tools")
        if allowed_tools is None:
            return None
        if not isinstance(allowed_tools, (list, tuple, set)) or not all(isinstance(item, str) for item in allowed_tools):
            return self._decision(
                "deny",
                "Invalid allowed_tools policy metadata.",
                tool_name=tool_name,
                matched_rule="agent.profile.allowed_tools.invalid",
                context=context,
                metadata={"known_tool": self._find_tool_spec(tool_name) is not None},
            )
        if tool_name in allowed_tools:
            return None
        return self._decision(
            "deny",
            f"Tool '{tool_name}' is not allowed for the current agent profile.",
            tool_name=tool_name,
            matched_rule="agent.profile.tool.deny",
            context=context,
            metadata={"known_tool": self._find_tool_spec(tool_name) is not None},
        )

    @staticmethod
    def _enum_value(value: Enum | str) -> str:
        return str(value.value if isinstance(value, Enum) else value)

    def _base_metadata(self, spec: ToolSpec, context: PolicyContext) -> dict[str, Any]:
        metadata = {
            "risk": self._enum_value(spec.risk),
            "side_effect": self._enum_value(spec.side_effect),
            "policy_mode": context.mode,
            "interactive": context.interactive,
            "external_impact": self._enum_value(spec.external_impact),
            "reversibility": self._enum_value(spec.reversibility),
        }
        metadata.update(spec.metadata or {})
        return metadata

    def _decision(
        self,
        decision: str,
        reason: str,
        *,
        tool_name: str,
        matched_rule: str,
        context: PolicyContext,
        metadata: dict[str, Any] | None = None,
        requires_approval: bool = False,
    ) -> PolicyDecision:
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("policy_mode", context.mode)
        merged_metadata.setdefault("requires_approval", requires_approval)
        return PolicyDecision(
            decision=cast(PolicyDecisionValue, decision),
            reason=reason,
            tool_name=tool_name,
            matched_rule=matched_rule,
            requires_approval=requires_approval,
            metadata=merged_metadata,
        )

    def _resolve_repo_root(
        self,
        *,
        arguments: dict[str, Any],
        context: PolicyContext,
        metadata: dict[str, Any],
    ) -> Path | None:
        raw_repo = arguments.get("repo") or context.repo
        if not isinstance(raw_repo, (str, Path)):
            return None
        if isinstance(raw_repo, str) and not raw_repo.strip():
            return None
        repo_path = Path(raw_repo).expanduser().resolve()
        metadata["repo"] = str(repo_path)
        metadata["repo_exists"] = repo_path.exists()
        metadata["repo_is_dir"] = repo_path.is_dir()
        return repo_path

    def _extract_target_paths(self, tool_name: str, arguments: dict[str, Any]) -> list[tuple[str, str]]:
        paths: list[tuple[str, str]] = []
        for field in ("path", "file", "file_path", "target_path"):
            value = arguments.get(field)
            if isinstance(value, str) and value.strip():
                paths.append((field, value))
        if tool_name == "apply_patch":
            patch = arguments.get("patch")
            if isinstance(patch, str) and patch.strip():
                paths.extend(("patch", path) for path in extract_paths_from_patch(patch))
        return paths

    def _normalize_target_path(self, *, repo_root: Path | None, raw_path: str) -> tuple[str | None, str | None]:
        cleaned = raw_path.strip().replace("\\", "/")
        if not cleaned:
            return None, "empty target path"
        raw_path_obj = Path(cleaned).expanduser()
        if raw_path_obj.is_absolute():
            absolute = raw_path_obj.resolve()
            if repo_root is None:
                return None, "absolute path is denied when repo root is unknown"
            try:
                relative = absolute.relative_to(repo_root)
            except ValueError:
                return None, "absolute path outside repo root is denied"
            return relative.as_posix() or ".", None
        if repo_root is not None:
            absolute = (repo_root / raw_path_obj).resolve()
            try:
                relative = absolute.relative_to(repo_root)
            except ValueError:
                return None, "relative path escaping repo root is denied"
            return relative.as_posix() or ".", None
        normalized = raw_path_obj.as_posix()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized or "."
        if ".." in Path(normalized).parts:
            return None, "relative path containing '..' is denied when repo root is unknown"
        return normalized, None

    def _match_any_glob(self, path: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            if fnmatch.fnmatch(path, pattern):
                return pattern
        return None

    def _match_write_scope(self, path: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            normalized_pattern = pattern.strip().replace("\\", "/").removeprefix("./")
            if (
                fnmatch.fnmatch(path, normalized_pattern)
                or path == normalized_pattern.rstrip("/")
                or path.startswith(normalized_pattern.rstrip("/") + "/")
            ):
                return pattern
        return None

    def _check_paths(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        repo_root: Path | None,
        context: PolicyContext,
        metadata: dict[str, Any],
    ) -> PolicyDecision | None:
        target_paths = self._extract_target_paths(tool_name, arguments)
        if tool_name == "apply_patch":
            patch = arguments.get("patch")
            if isinstance(patch, str) and patch.strip() and not any(field == "patch" for field, _ in target_paths):
                patch_metadata = dict(metadata)
                patch_metadata.update({"patch_paths_extracted": 0, "path_field": "patch"})
                return self._decision(
                    "deny",
                    "Patch does not contain extractable file paths; refusing to apply without path policy check.",
                    tool_name=tool_name,
                    matched_rule="patch.paths.missing.deny",
                    context=context,
                    metadata=patch_metadata,
                )

        for field, raw_path in target_paths:
            normalized, error = self._normalize_target_path(repo_root=repo_root, raw_path=raw_path)
            path_metadata = dict(metadata)
            path_metadata.update({"path_field": field, "raw_path": raw_path, "normalized_path": normalized})
            if error is not None:
                path_metadata["path_error"] = error
                return self._decision(
                    "deny",
                    error,
                    tool_name=tool_name,
                    matched_rule="path.boundary.deny",
                    context=context,
                    metadata=path_metadata,
                )
            assert normalized is not None
            matched = self._match_any_glob(normalized, self.config.paths.deny)
            if matched is not None:
                path_metadata["path_rule"] = matched
                return self._decision(
                    "deny",
                    f"Path '{normalized}' is denied by policy rule '{matched}'.",
                    tool_name=tool_name,
                    matched_rule=f"path.deny.{matched}",
                    context=context,
                    metadata=path_metadata,
                )
            if tool_name in STRUCTURED_WRITE_TOOLS and "write_scope" in context.metadata:
                raw_scope = context.metadata["write_scope"]
                if not isinstance(raw_scope, (list, tuple, set)) or not all(isinstance(item, str) for item in raw_scope):
                    path_metadata["write_scope"] = raw_scope
                    return self._decision(
                        "deny",
                        "Invalid write_scope policy metadata.",
                        tool_name=tool_name,
                        matched_rule="agent.profile.write_scope.invalid",
                        context=context,
                        metadata=path_metadata,
                    )
                scope = list(raw_scope)
                scope_match = self._match_write_scope(normalized, scope)
                if scope_match is None:
                    path_metadata["write_scope"] = scope
                    return self._decision(
                        "deny",
                        f"Path '{normalized}' is outside the current agent write_scope.",
                        tool_name=tool_name,
                        matched_rule="agent.profile.write_scope.deny",
                        context=context,
                        metadata=path_metadata,
                    )
                path_metadata["write_scope_rule"] = scope_match
        if tool_name == "git_diff" and arguments.get("include_content") is True and not arguments.get("path"):
            return self._decision(
                "deny",
                "git_diff include_content=True requires a specific path to avoid leaking repository-wide diffs.",
                tool_name=tool_name,
                matched_rule="git_diff.content_without_path.deny",
                context=context,
                metadata=dict(metadata),
            )
        return None

    @staticmethod
    def _normalize_command(command: str) -> str:
        return re.sub(r"\s+", " ", command.strip())

    def _extract_command_values(self, arguments: dict[str, Any]) -> list[tuple[str, str]]:
        command_values: list[tuple[str, str]] = []
        for field in ("command", "cmd", "shell"):
            value = arguments.get(field)
            if isinstance(value, str) and value.strip():
                command_values.append((field, value))
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                command_values.append((field, " ".join(value)))
        return command_values

    @staticmethod
    def _shell_segments(command: str) -> list[list[str]]:
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return []

        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token and all(char in ";&|" for char in token):
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(current)
        return segments

    def _matches_hard_deny(self, command: str) -> str | None:
        segments = self._shell_segments(command)
        for pattern in self.config.commands.hard_deny_patterns:
            try:
                pattern_tokens = shlex.split(self._normalize_command(pattern))
            except ValueError:
                continue
            if not pattern_tokens:
                continue
            for segment in segments:
                candidate = segment[1:] if segment and segment[0] == "sudo" else segment
                if candidate[: len(pattern_tokens)] == pattern_tokens:
                    return pattern
        return None

    def _check_hard_deny_command(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: PolicyContext,
        metadata: dict[str, Any],
    ) -> PolicyDecision | None:
        for field, raw_command in self._extract_command_values(arguments):
            normalized_command = self._normalize_command(raw_command)
            command_metadata = dict(metadata)
            command_metadata.update({"command_field": field, "raw_command": raw_command, "normalized_command": normalized_command})
            if tool_name in {"run_shell", "run_tests"}:
                for token in PROTECTED_COMMAND_TOKENS:
                    if token in normalized_command:
                        command_metadata["command_rule"] = token
                        return self._decision(
                            "deny",
                            f"Command references protected path token '{token}'.",
                            tool_name=tool_name,
                            matched_rule="command.protected_path_token.deny",
                            context=context,
                            metadata=command_metadata,
                        )
            hard_deny = self._matches_hard_deny(normalized_command)
            if hard_deny is not None:
                command_metadata["command_rule"] = hard_deny
                return self._decision(
                    "deny",
                    f"Command is denied by hard safety rule '{hard_deny}'.",
                    tool_name=tool_name,
                    matched_rule=f"command.hard_deny.{hard_deny}",
                    context=context,
                    metadata=command_metadata,
                )
        return None

    def _decide_effect(
        self,
        *,
        tool_name: str,
        effect: ActionEffect,
        context: PolicyContext,
        metadata: dict[str, Any],
    ) -> PolicyDecision:
        if context.mode == "read_only":
            if effect.external_impact == ExternalImpact.WRITE:
                return self._decision(
                    "deny",
                    f"read_only mode forbids state-changing external action '{tool_name}'.",
                    tool_name=tool_name,
                    matched_rule="mode.read_only.external_write.deny",
                    context=context,
                    metadata=metadata,
                )
            if effect.reversibility in {Reversibility.REVERSIBLE, Reversibility.IRREVERSIBLE} or (
                effect.reversibility == Reversibility.UNKNOWN
                and metadata.get("side_effect") == "local_write"
            ):
                return self._decision(
                    "deny",
                    f"read_only mode forbids state-changing action '{tool_name}'.",
                    tool_name=tool_name,
                    matched_rule="mode.read_only.mutation.deny",
                    context=context,
                    metadata=metadata,
                )

        if context.mode == "danger":
            return self._decision(
                "allow",
                f"unsafe_auto allows '{tool_name}' after hard-boundary checks.",
                tool_name=tool_name,
                matched_rule="mode.danger.allow",
                context=context,
                metadata=metadata,
            )

        if effect.external_impact == ExternalImpact.NONE and effect.reversibility in {
            Reversibility.NOT_APPLICABLE,
            Reversibility.REVERSIBLE,
        }:
            return self._decision(
                "allow",
                f"'{tool_name}' stays local and is non-mutating or reversible.",
                tool_name=tool_name,
                matched_rule="effect.local_safe.allow",
                context=context,
                metadata=metadata,
            )

        return self._decision(
            "ask",
            f"'{tool_name}' may affect external state or cannot be safely reversed.",
            tool_name=tool_name,
            matched_rule="effect.approval.ask",
            requires_approval=True,
            context=context,
            metadata=metadata,
        )
