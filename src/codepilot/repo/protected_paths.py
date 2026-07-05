from __future__ import annotations


DEFAULT_REPO_PROTECTED_PATHS = [
    ".git",
    ".git/**",
    ".github/workflows",
    ".github/workflows/**",
    ".codepilot",
    ".codepilot/**",
    "runs",
    "runs/**",
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
]
