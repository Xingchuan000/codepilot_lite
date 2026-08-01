from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestScope:
    framework: str
    selectors: tuple[str, ...]
    full_suite: bool
    executing: bool = True

    @property
    def key(self) -> str:
        return f"{self.framework}:{'|'.join(self.selectors) if self.selectors else '*'}"


_OPTIONS_WITH_VALUES = {
    "-k",
    "-m",
    "--maxfail",
    "--tb",
    "--rootdir",
    "--confcutdir",
    "--ignore",
    "--ignore-glob",
}
_NON_EXECUTING_OPTIONS = {"--collect-only", "--co", "--fixtures", "--fixtures-per-test", "--funcargs"}


def parse_test_scope(command: str) -> TestScope | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    pytest_index = _pytest_index(tokens)
    if pytest_index is None:
        return None
    selectors: list[str] = []
    has_filter = False
    executing = True
    index = pytest_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token in _NON_EXECUTING_OPTIONS or any(token.startswith(f"{option}=") for option in _NON_EXECUTING_OPTIONS):
            executing = False
            index += 1
            continue
        option, separator, value = token.partition("=")
        if option in _OPTIONS_WITH_VALUES:
            has_filter |= option in {"-k", "-m", "--ignore", "--ignore-glob"}
            if not separator:
                value = tokens[index + 1] if index + 1 < len(tokens) else ""
                index += 1
            if option in {"-k", "-m", "--ignore", "--ignore-glob"}:
                selectors.append(f"{option}={value}")
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        selectors.append(token)
        index += 1
    normalized = tuple(_normalize_selector(selector) for selector in selectors)
    full_suite = not has_filter and all(_is_directory_selector(selector) for selector in normalized)
    return TestScope("pytest", () if full_suite else normalized, full_suite, executing)


def exact_command_scope(command: str) -> TestScope:
    return TestScope("command", (" ".join(command.split())[:300],), False)


def _pytest_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        if token in {"pytest", "py.test"}:
            return index
    if len(tokens) >= 3 and Path(tokens[0]).name.startswith("python") and tokens[1:3] == ["-m", "pytest"]:
        return 2
    return None


def _normalize_selector(selector: str) -> str:
    return selector.replace("\\", "/")


def _is_directory_selector(selector: str) -> bool:
    return selector in {".", "tests", "tests/"}
