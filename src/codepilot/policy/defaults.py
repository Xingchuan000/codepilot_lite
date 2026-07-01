from __future__ import annotations

from codepilot.policy.config import CommandPolicyConfig, PathPolicyConfig, PolicyConfig, ToolPolicyConfig


def default_policy_config() -> PolicyConfig:
    """返回第五步使用的默认策略配置。"""

    return PolicyConfig(
        tools=ToolPolicyConfig(
            allow=["list_files", "read_file", "search_code"],
            ask=["run_shell", "apply_patch", "replace_range"],
            deny=[],
        ),
        paths=PathPolicyConfig(
            allow=["**"],
            deny=[
                ".env",
                ".env.*",
                "**/.env",
                "**/.env.*",
                "secrets",
                "secrets/**",
                "**/secrets",
                "**/secrets/**",
                ".ssh",
                ".ssh/**",
                "**/.ssh",
                "**/.ssh/**",
            ],
        ),
        commands=CommandPolicyConfig(
            allow_prefixes=[
                "pytest",
                "python -m pytest",
                "ruff",
                "mypy",
                "npm test",
                "npm run test",
                "npm run lint",
            ],
            deny_substrings=[
                "rm -rf",
                "git push",
                "git reset --hard",
                "npm publish",
                "curl ",
                "wget ",
                "ssh ",
                "scp ",
                "chmod 777",
            ],
        ),
    )
