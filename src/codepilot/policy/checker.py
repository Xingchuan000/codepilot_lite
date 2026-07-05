from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, cast

from codepilot.policy.config import PolicyConfig
from codepilot.policy.defaults import default_policy_config
from codepilot.policy.models import PolicyContext, PolicyDecision, PolicyDecisionValue
from codepilot.router.actions import ToolAction
from codepilot.tools.base import DefaultPermission, ToolSideEffect, ToolSpec
from codepilot.tools.patch_utils import extract_paths_from_patch
from codepilot.tools.registry import find_tool_spec

COMMAND_TOOLS = {"run_shell", "run_tests"}
PROTECTED_COMMAND_TOKENS = [".env", ".github/workflows", ".codepilot", "secrets/", ".ssh"]


class PolicyChecker:
    """用一组简单、可解释的规则判断工具动作是否可以执行。"""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or default_policy_config()

    @classmethod
    def default(cls) -> "PolicyChecker":
        return cls(default_policy_config())

    def check(self, action: ToolAction, context: PolicyContext | None = None) -> PolicyDecision:
        """判断一次结构化工具动作是否可以放行。"""

        context = context or PolicyContext()
        tool_name = action.tool_name
        arguments = dict(action.arguments or {})
        spec = find_tool_spec(tool_name)

        if spec is None:
            return self._decision(
                "allow",
                "Unknown tool is allowed to reach the registry so it can return a structured unknown-tool error.",
                tool_name=tool_name,
                matched_rule="tool.unknown.allow_to_registry",
                context=context,
                metadata={"known_tool": False},
            )

        metadata = self._base_metadata(spec=spec, context=context)
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

        command_decision = self._check_command(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            metadata=metadata,
        )
        if command_decision is not None:
            return command_decision

        if context.mode == "read_only" and self._enum_value(spec.side_effect) != self._enum_value(ToolSideEffect.NONE):
            return self._decision(
                "deny",
                f"Policy mode read_only denies tool '{tool_name}' because it has side effect '{self._enum_value(spec.side_effect)}'.",
                tool_name=tool_name,
                matched_rule="mode.read_only.side_effect.deny",
                context=context,
                metadata=metadata,
            )

        if tool_name in self.config.tools.deny:
            return self._decision(
                "deny",
                f"Tool '{tool_name}' is denied by policy config.",
                tool_name=tool_name,
                matched_rule=f"tool.deny.{tool_name}",
                context=context,
                metadata=metadata,
            )

        if tool_name in COMMAND_TOOLS:
            allow_decision = self._allow_safe_command_prefix(
                tool_name=tool_name,
                arguments=arguments,
                context=context,
                metadata=metadata,
            )
            if allow_decision is not None:
                return allow_decision

        default_permission = self._enum_value(spec.default_permission)

        if default_permission == self._enum_value(DefaultPermission.DENY):
            return self._decision(
                "deny",
                f"Tool '{tool_name}' default permission is deny.",
                tool_name=tool_name,
                matched_rule="tool.default_permission.deny",
                context=context,
                metadata=metadata,
            )

        if default_permission == self._enum_value(DefaultPermission.ASK):
            return self._decision(
                "ask",
                f"Tool '{tool_name}' requires approval before execution.",
                tool_name=tool_name,
                matched_rule="tool.default_permission.ask",
                requires_approval=True,
                context=context,
                metadata=metadata,
            )

        if tool_name in self.config.tools.ask:
            return self._decision(
                "ask",
                f"Tool '{tool_name}' requires approval by policy config.",
                tool_name=tool_name,
                matched_rule=f"tool.ask.{tool_name}",
                requires_approval=True,
                context=context,
                metadata=metadata,
            )

        if tool_name in self.config.tools.allow:
            return self._decision(
                "allow",
                f"Tool '{tool_name}' is allowed by policy config.",
                tool_name=tool_name,
                matched_rule=f"tool.allow.{tool_name}",
                context=context,
                metadata=metadata,
            )

        if default_permission == self._enum_value(DefaultPermission.ALLOW):
            return self._decision(
                "allow",
                f"Tool '{tool_name}' default permission is allow.",
                tool_name=tool_name,
                matched_rule="tool.default_permission.allow",
                context=context,
                metadata=metadata,
            )

        return self._decision(
            "ask",
            f"Tool '{tool_name}' has no explicit policy match and requires approval by default.",
            tool_name=tool_name,
            matched_rule="tool.fallback.ask",
            requires_approval=True,
            context=context,
            metadata=metadata,
        )

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    def _base_metadata(self, spec: ToolSpec, context: PolicyContext) -> dict[str, Any]:
        return {
            "risk": self._enum_value(spec.risk),
            "side_effect": self._enum_value(spec.side_effect),
            "default_permission": self._enum_value(spec.default_permission),
            "policy_mode": context.mode,
            "approved": context.approved,
            "interactive": context.interactive,
        }

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
        merged_metadata.setdefault("approved", context.approved)
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
        path_fields = ("path", "file", "file_path", "target_path")
        paths: list[tuple[str, str]] = []
        for field in path_fields:
            value = arguments.get(field)
            if isinstance(value, str) and value.strip():
                paths.append((field, value))
        if tool_name == "apply_patch":
            patch = arguments.get("patch")
            if isinstance(patch, str) and patch.strip():
                for touched_path in extract_paths_from_patch(patch):
                    paths.append(("patch", touched_path))
        return paths

    def _normalize_target_path(
        self,
        *,
        repo_root: Path | None,
        raw_path: str,
    ) -> tuple[str | None, str | None]:
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
            patch_is_non_empty = isinstance(patch, str) and bool(patch.strip())
            has_patch_paths = any(field == "patch" for field, _ in target_paths)
            if patch_is_non_empty and not has_patch_paths:
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
        return None

    def _normalize_command(self, command: str) -> str:
        return re.sub(r"\s+", " ", command.strip())

    def _matches_command_prefix(self, command: str, prefixes: list[str]) -> str | None:
        for prefix in prefixes:
            normalized_prefix = self._normalize_command(prefix)
            if command == normalized_prefix or command.startswith(normalized_prefix + " "):
                return prefix
        return None

    def _matches_denied_command(self, command: str, denied_rules: list[str]) -> str | None:
        padded_command = f" {command} "
        for denied in denied_rules:
            normalized_denied = self._normalize_command(denied)
            if denied.endswith(" "):
                token = normalized_denied
                if command == token or command.startswith(token + " ") or f" {token} " in padded_command:
                    return denied
                continue
            if normalized_denied in command:
                return denied
        return None

    def _check_command(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: PolicyContext,
        metadata: dict[str, Any],
    ) -> PolicyDecision | None:
        if tool_name == "git_diff" and arguments.get("include_content") is True and not arguments.get("path"):
            return self._decision(
                "deny",
                "git_diff include_content=True requires a specific path to avoid leaking repository-wide diffs.",
                tool_name=tool_name,
                matched_rule="git_diff.content_without_path.deny",
                context=context,
                metadata=dict(metadata),
            )
        if tool_name not in COMMAND_TOOLS:
            return None
        raw_command = arguments.get("command")
        if not isinstance(raw_command, str):
            return None

        normalized_command = self._normalize_command(raw_command)
        command_metadata = dict(metadata)
        command_metadata.update({"raw_command": raw_command, "normalized_command": normalized_command})
        if tool_name == "run_shell":
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

        denied = self._matches_denied_command(normalized_command, self.config.commands.deny_substrings)
        if denied is not None:
            command_metadata["command_rule"] = denied
            return self._decision(
                "deny",
                f"Command is denied by policy rule '{denied}'.",
                tool_name=tool_name,
                matched_rule=f"command.deny_substrings.{denied}",
                context=context,
                metadata=command_metadata,
            )
        return None

    def _allow_safe_command_prefix(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: PolicyContext,
        metadata: dict[str, Any],
    ) -> PolicyDecision | None:
        if context.mode not in {"build", "danger"}:
            return None
        raw_command = arguments.get("command")
        if not isinstance(raw_command, str):
            return None
        normalized_command = self._normalize_command(raw_command)
        matched = self._matches_command_prefix(normalized_command, self.config.commands.allow_prefixes)
        if matched is None:
            return None
        command_metadata = dict(metadata)
        command_metadata.update(
            {
                "raw_command": raw_command,
                "normalized_command": normalized_command,
                "command_rule": matched,
            }
        )
        return self._decision(
            "allow",
            f"Command is allowed in {context.mode} mode by safe prefix '{matched}'.",
            tool_name=tool_name,
            matched_rule=f"command.allow_prefixes.{matched}",
            context=context,
            metadata=command_metadata,
        )
