from pathlib import Path

from codepilot.tools.base import ToolRisk
from codepilot.tools.search_tools import DEFAULT_EXCLUDE_DIRS, search_code


def test_search_code_finds_match(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tool.py").write_text("from codepilot.tools.base import ToolResult\n", encoding="utf-8")

    result = search_code(tmp_path, query="ToolResult", path="src")

    assert result.success is True
    assert result.output == "src/tool.py:1: from codepilot.tools.base import ToolResult"
    assert result.metadata["risk"] == ToolRisk.READ_ONLY.value
    assert result.output_summary == "Found 1 matches for 'ToolResult'."


def test_search_code_no_match(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("print('hello')\n", encoding="utf-8")

    result = search_code(tmp_path, query="missing", path="pkg")

    assert result.success is True
    assert result.output == "No matches found."
    assert result.output_summary == "No matches found for 'missing'."


def test_search_code_empty_query_error(tmp_path: Path) -> None:
    result = search_code(tmp_path, query="", path=".")

    assert result.success is False
    assert "empty" in result.error


def test_search_code_path_escape_blocked(tmp_path: Path) -> None:
    result = search_code(tmp_path, query="anything", path="../outside")

    assert result.success is False
    assert "escapes repository root" in result.error


def test_search_code_respects_file_glob(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "match.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "pkg" / "skip.txt").write_text("needle\n", encoding="utf-8")

    result = search_code(tmp_path, query="needle", path="pkg", file_glob="*.py")

    assert result.success is True
    assert result.output == "pkg/match.py:1: needle"


def test_search_code_case_insensitive_by_default(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("ToolResult\n", encoding="utf-8")

    result = search_code(tmp_path, query="toolresult", path=".")

    assert result.success is True
    assert "a.py:1: ToolResult" in result.output


def test_search_code_case_sensitive_option(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("ToolResult\n", encoding="utf-8")

    result = search_code(tmp_path, query="toolresult", path=".", case_sensitive=True)

    assert result.success is True
    assert result.output == "No matches found."


def test_search_code_skips_excluded_dirs(tmp_path: Path) -> None:
    for directory in DEFAULT_EXCLUDE_DIRS:
        hidden_dir = tmp_path / directory
        hidden_dir.mkdir()
        (hidden_dir / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "visible").mkdir()
    (tmp_path / "visible" / "a.py").write_text("needle\n", encoding="utf-8")

    result = search_code(tmp_path, query="needle", path=".")

    assert result.success is True
    assert result.output == "visible/a.py:1: needle"


def test_search_code_truncates_max_results(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    for index in range(5):
        (tmp_path / "pkg" / f"file_{index}.py").write_text("needle\n", encoding="utf-8")

    result = search_code(tmp_path, query="needle", path="pkg", max_results=3)

    assert result.success is True
    assert len(result.output.splitlines()) == 4  # 3 条结果 + 1 行截断提示
    assert result.metadata["truncated"] is True
    assert "truncated after 3 results" in result.output
