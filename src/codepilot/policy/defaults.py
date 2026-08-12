from __future__ import annotations

from codepilot.policy.config import CommandPolicyConfig, PathPolicyConfig, PolicyConfig
from codepilot.repo.protected_paths import DEFAULT_REPO_PROTECTED_PATHS


def default_policy_config() -> PolicyConfig:
    """返回第五步使用的默认策略配置。"""

    return PolicyConfig(
        paths=PathPolicyConfig(
            deny=[*DEFAULT_REPO_PROTECTED_PATHS],
        ),
        commands=CommandPolicyConfig(
            hard_deny_patterns=[
                "rm -rf /",
                "rm -rf ~",
            ],
        ),
    )
