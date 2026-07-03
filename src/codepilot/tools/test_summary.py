from __future__ import annotations

"""pytest 输出摘要器。

这个模块只负责把测试输出压缩成结构化摘要，不执行任何命令，也不读写文件。
第七步只实现计划里要求的 pytest 文本提取规则，不额外扩展更多测试框架适配。
"""

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class TestOutputSummary:
    """测试输出摘要结果。

    这里保留最小但够用的信息，方便工具层把长测试日志整理成更短的可读结果。
    """

    status: str
    summary_line: str | None
    failed_tests: list[str] = field(default_factory=list)
    error_lines: list[str] = field(default_factory=list)
    relevant_output: str = ""
    failed_tests_truncated: bool = False
    relevant_output_truncated: bool = False


_PYTEST_SUMMARY_RE = re.compile(
    r"^(?:\d+ .+?|no tests ran) in \d+(?:\.\d+)?s$",
)
_FAILED_TEST_RE = re.compile(r"^FAILED (?P<name>\S+)\s+-\s+.*$")


def _looks_like_pytest_summary(line: str) -> bool:
    """判断一行是否像 pytest 末尾摘要。"""

    return bool(_PYTEST_SUMMARY_RE.match(line.strip()))


def _extract_failed_test_name(line: str) -> str | None:
    """从 FAILED 行中提取失败测试名。"""

    match = _FAILED_TEST_RE.match(line.strip())
    return match.group("name") if match else None


def _is_relevant_error_line(line: str) -> bool:
    """筛选出对定位失败最有帮助的关键行。"""

    stripped = line.strip()
    return (
        line.startswith("FAILED ")
        or "ERROR " in line
        or "Traceback" in line
        or "AssertionError" in line
        or line.startswith("E   ")
        or line.startswith("E       ")
        or stripped.startswith("E   ")
        or stripped.startswith("E       ")
    )


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """去重并保持原始顺序。"""

    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _truncate_chars(text: str, max_chars: int) -> tuple[str, bool]:
    """按字符数截断文本。"""

    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def summarize_test_output(
    output: str,
    *,
    returncode: int,
    timed_out: bool = False,
    max_failed_tests: int = 10,
    max_relevant_lines: int = 80,
    max_chars: int = 6000,
) -> TestOutputSummary:
    """把 pytest 文本输出整理成结构化摘要。

    这里严格按计划提取：
    - 最后一个 pytest 摘要行
    - 失败测试名
    - 关键错误线索
    - 适合给模型阅读的短日志
    """

    status = "timed_out" if timed_out else ("passed" if returncode == 0 else "failed")
    lines = output.splitlines()

    summary_line = None
    for line in lines:
        if _looks_like_pytest_summary(line):
            summary_line = line.strip()

    failed_tests = _dedupe_keep_order(
        [name for line in lines if (name := _extract_failed_test_name(line)) is not None]
    )
    failed_tests_truncated = len(failed_tests) > max_failed_tests
    failed_tests = failed_tests[:max_failed_tests]

    error_lines = _dedupe_keep_order([line for line in lines if _is_relevant_error_line(line)])

    relevant_lines = list(error_lines)
    if summary_line is not None and summary_line not in relevant_lines:
        relevant_lines.append(summary_line)

    if not relevant_lines:
        tail_count = min(len(lines), max_relevant_lines)
        relevant_lines = lines[-tail_count:]
    elif len(relevant_lines) < max_relevant_lines:
        needed = max_relevant_lines - len(relevant_lines)
        for line in lines[-needed:]:
            if line not in relevant_lines:
                relevant_lines.append(line)
            if len(relevant_lines) >= max_relevant_lines:
                break

    line_truncated = len(relevant_lines) > max_relevant_lines
    relevant_lines = relevant_lines[:max_relevant_lines]
    relevant_output, char_truncated = _truncate_chars("\n".join(relevant_lines), max_chars)

    return TestOutputSummary(
        status=status,
        summary_line=summary_line,
        failed_tests=failed_tests,
        error_lines=error_lines,
        relevant_output=relevant_output,
        failed_tests_truncated=failed_tests_truncated,
        relevant_output_truncated=line_truncated or char_truncated,
    )
