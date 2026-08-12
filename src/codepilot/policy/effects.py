from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codepilot.tools.base import ExternalImpact, Reversibility, ToolSpec


@dataclass(frozen=True)
class ActionEffect:
    external_impact: ExternalImpact
    reversibility: Reversibility
    reason: str


LOCAL_READ_COMMANDS = {"pwd", "ls", "cat", "grep", "rg"}
GIT_READ_SUBCOMMANDS = {"status", "diff", "log", "show", "branch"}
LOCAL_VERIFY_COMMAND_PREFIXES = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("ruff",),
    ("mypy",),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("npm", "run", "lint"),
    ("pnpm", "test"),
    ("pnpm", "run", "test"),
    ("yarn", "test"),
)
COMPLEX_SHELL_TOKENS = ("|", ";", "&&", "||", "$(", "`", ">", "<", "\n", "\r")


def classify_shell_command(command: str) -> ActionEffect:
    if any(token in command for token in COMPLEX_SHELL_TOKENS):
        return ActionEffect(ExternalImpact.UNKNOWN, Reversibility.UNKNOWN, "shell.complex_unknown")

    try:
        tokens = shlex.split(command)
    except ValueError:
        return ActionEffect(ExternalImpact.UNKNOWN, Reversibility.UNKNOWN, "shell.unknown")
    if not tokens:
        return ActionEffect(ExternalImpact.UNKNOWN, Reversibility.UNKNOWN, "shell.unknown")

    executable = Path(tokens[0]).name
    if executable in LOCAL_READ_COMMANDS:
        return ActionEffect(ExternalImpact.NONE, Reversibility.NOT_APPLICABLE, "shell.local_read")
    if any(tuple(tokens[: len(prefix)]) == prefix for prefix in LOCAL_VERIFY_COMMAND_PREFIXES):
        return ActionEffect(ExternalImpact.NONE, Reversibility.NOT_APPLICABLE, "shell.local_verification")
    if len(tokens) >= 2 and executable == "git" and tokens[1] in GIT_READ_SUBCOMMANDS:
        return ActionEffect(ExternalImpact.NONE, Reversibility.NOT_APPLICABLE, "shell.git_read")
    if len(tokens) >= 2 and executable == "git" and tokens[1] in {"push", "fetch", "pull"}:
        impact = ExternalImpact.WRITE if tokens[1] == "push" else ExternalImpact.READ
        reason = "shell.git_push" if tokens[1] == "push" else "shell.git_remote_read"
        return ActionEffect(impact, Reversibility.UNKNOWN if impact == ExternalImpact.WRITE else Reversibility.NOT_APPLICABLE, reason)
    if executable in {"curl", "wget"}:
        return ActionEffect(ExternalImpact.READ, Reversibility.NOT_APPLICABLE, "shell.network")
    if len(tokens) >= 2 and executable in {"npm", "pnpm"} and tokens[1] == "publish":
        return ActionEffect(ExternalImpact.WRITE, Reversibility.UNKNOWN, "shell.publish")
    if len(tokens) >= 2 and executable in {"git", "pip", "npm", "pnpm", "yarn"} and tokens[1] in {"install", "fetch", "pull"}:
        return ActionEffect(ExternalImpact.READ, Reversibility.NOT_APPLICABLE, "shell.package_or_remote_read")
    if executable == "rm" or (len(tokens) >= 2 and executable == "git" and tokens[1] == "reset" and "--hard" in tokens[2:]):
        return ActionEffect(ExternalImpact.NONE, Reversibility.IRREVERSIBLE, "shell.local_irreversible")
    return ActionEffect(ExternalImpact.UNKNOWN, Reversibility.UNKNOWN, "shell.unknown")


def classify_shell_action(tool_name: str, arguments: dict[str, Any]) -> ActionEffect:
    command = arguments.get("command")
    if not isinstance(command, str):
        return ActionEffect(ExternalImpact.UNKNOWN, Reversibility.UNKNOWN, "shell.unknown")
    return classify_shell_command(command)


class ActionEffectClassifier:
    def classify(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        spec: ToolSpec,
    ) -> ActionEffect:
        if tool_name in {"run_shell", "run_tests"}:
            return classify_shell_action(tool_name, arguments)
        return ActionEffect(spec.external_impact, spec.reversibility, "tool_spec")
