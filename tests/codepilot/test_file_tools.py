from pathlib import Path

import pytest

from codepilot.tools.base import ToolResult, ToolRisk
from codepilot.tools.file_tools import (
    DEFAULT_LIST_FILES_PAGE_SIZE,
    LIST_FILES_PAGE_MAX_CHARS,
    MAX_LIST_FILES_PAGE_SIZE,
    list_files,
    read_file,
)


def _collect_list_files_pages(
    repo: Path,
    *,
    path: str = ".",
    max_depth: int = 2,
    include_hidden: bool = False,
    max_entries: int = DEFAULT_LIST_FILES_PAGE_SIZE,
) -> tuple[list[ToolResult], list[str]]:
    """按 offset 顺序把 list_files 的分页结果收集起来。"""

    pages: list[ToolResult] = []
    merged: list[str] = []
    offset = 0

    while True:
        result = list_files(
            repo,
            path=path,
            max_depth=max_depth,
            include_hidden=include_hidden,
            max_entries=max_entries,
            offset=offset,
        )
        assert result.success is True
        pages.append(result)
        merged.extend(result.output.splitlines() if result.output else [])
        if not result.metadata["has_more"]:
            return pages, merged
        offset = result.metadata["next_offset"]


def test_list_files_basic(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("assert True\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=2)

    assert result.success is True
    assert result.output.splitlines() == ["src/", "src/main.py", "tests/", "tests/test_main.py"]
    assert result.output_summary == "Listed 4 entries under ."
    assert result.metadata["entries_returned"] == 4
    assert result.metadata["has_more"] is False
    assert result.metadata["next_offset"] is None
    assert result.metadata["truncated"] is False
    assert result.metadata["limit_reason"] == "end"
    assert result.metadata["risk"] == ToolRisk.READ_ONLY.value
    assert result.metadata["duration_ms"] >= 0


def test_list_files_hidden_default_excluded(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret\n", encoding="utf-8")
    (tmp_path / "visible").mkdir()
    (tmp_path / "visible" / ".env").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "visible" / "ok.txt").write_text("ok\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=2)

    assert result.success is True
    assert ".git" not in result.output
    assert ".env" not in result.output
    assert "visible/" in result.output
    assert "visible/ok.txt" in result.output


def test_list_files_path_escape_blocked(tmp_path: Path) -> None:
    result = list_files(tmp_path, path="../outside")

    assert result.success is False
    assert "escapes repository root" in result.error


def test_list_files_does_not_follow_external_directory_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    (repo / "external").symlink_to(outside, target_is_directory=True)

    result = list_files(repo, path=".", max_depth=2)

    assert result.success is True
    assert result.output.splitlines() == ["external@"]
    assert "secret.txt" not in result.output
    assert result.metadata["follow_symlinks"] is False


def test_list_files_does_not_follow_internal_directory_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real").mkdir()
    (repo / "real" / "a.txt").write_text("a\n", encoding="utf-8")
    (repo / "zz_alias").symlink_to(repo / "real", target_is_directory=True)

    result = list_files(repo, path=".", max_depth=2)

    assert result.success is True
    assert result.output.splitlines() == ["real/", "real/a.txt", "zz_alias@"]
    assert "zz_alias/a.txt" not in result.output
    assert result.metadata["follow_symlinks"] is False


def test_list_files_symlink_loop_does_not_recurse(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "loop").symlink_to(repo, target_is_directory=True)

    result = list_files(repo, path=".", max_depth=4)

    assert result.success is True
    assert result.output.splitlines() == ["loop@"]
    assert "loop/loop" not in result.output
    assert result.metadata["follow_symlinks"] is False


def test_list_files_first_page_returns_next_offset(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"file_{index}.txt").write_text(f"{index}\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=1, max_entries=3)

    assert result.success is True
    assert result.output.splitlines() == ["file_0.txt", "file_1.txt", "file_2.txt"]
    assert result.metadata["entries_returned"] == 3
    assert result.metadata["has_more"] is True
    assert result.metadata["next_offset"] == 3
    assert result.metadata["truncated"] is True
    assert result.metadata["limit_reason"] == "entry_limit"
    assert result.output_summary == "Listed 3 entries under . from offset 0; more entries available at offset 3."


def test_list_files_second_page_continues_without_duplicates(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"file_{index}.txt").write_text(f"{index}\n", encoding="utf-8")

    first_page = list_files(tmp_path, path=".", max_depth=1, max_entries=3)
    second_page = list_files(tmp_path, path=".", max_depth=1, max_entries=3, offset=first_page.metadata["next_offset"])

    assert first_page.success is True
    assert second_page.success is True
    assert first_page.output.splitlines() == ["file_0.txt", "file_1.txt", "file_2.txt"]
    assert second_page.output.splitlines() == ["file_3.txt", "file_4.txt"]
    assert set(first_page.output.splitlines()).isdisjoint(second_page.output.splitlines())
    assert first_page.output.splitlines() + second_page.output.splitlines() == [f"file_{index}.txt" for index in range(5)]
    assert second_page.metadata["has_more"] is False
    assert second_page.metadata["next_offset"] is None


def test_list_files_exact_boundary_does_not_report_truncated(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"file_{index}.txt").write_text(f"{index}\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=1, max_entries=3)

    assert result.success is True
    assert result.metadata["entries_returned"] == 3
    assert result.metadata["has_more"] is False
    assert result.metadata["truncated"] is False
    assert result.metadata["next_offset"] is None
    assert result.metadata["limit_reason"] == "end"


def test_list_files_offset_beyond_end_returns_empty_page(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"file_{index}.txt").write_text(f"{index}\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=1, offset=1000)

    assert result.success is True
    assert result.output == ""
    assert result.metadata["entries_returned"] == 0
    assert result.metadata["has_more"] is False
    assert result.metadata["next_offset"] is None
    assert result.metadata["limit_reason"] == "end"


@pytest.mark.parametrize(
    ("arguments", "error_text"),
    [
        ({"offset": -1}, "offset"),
        ({"max_entries": 0}, "max_entries"),
        ({"max_entries": -1}, "max_entries"),
        ({"max_entries": MAX_LIST_FILES_PAGE_SIZE + 1}, "max_entries"),
    ],
)
def test_list_files_rejects_invalid_pagination_arguments(tmp_path: Path, arguments: dict[str, int], error_text: str) -> None:
    result = list_files(tmp_path, path=".", max_depth=1, **arguments)

    assert result.success is False
    assert error_text in result.error


def test_list_files_include_hidden_is_consistent_across_pages(tmp_path: Path) -> None:
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "visible").mkdir()
    (tmp_path / "visible" / "deep").mkdir()
    (tmp_path / "visible" / "deep" / "inner.txt").write_text("inner\n", encoding="utf-8")
    (tmp_path / "visible" / "plain.txt").write_text("plain\n", encoding="utf-8")

    pages, merged = _collect_list_files_pages(tmp_path, max_depth=2, include_hidden=False, max_entries=1)

    assert all(".hidden" not in page.output for page in pages)
    assert merged == ["visible/", "visible/deep/", "visible/plain.txt"]

    hidden_pages, hidden_merged = _collect_list_files_pages(tmp_path, max_depth=2, include_hidden=True, max_entries=1)

    assert any(".hidden/" in page.output for page in hidden_pages)
    assert hidden_merged == [".hidden/", ".hidden/secret.txt", "visible/", "visible/deep/", "visible/plain.txt"]


def test_list_files_max_depth_is_consistent_across_pages(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.txt").write_text("deep\n", encoding="utf-8")
    (tmp_path / "root.txt").write_text("root\n", encoding="utf-8")

    shallow_pages, shallow_merged = _collect_list_files_pages(tmp_path, max_depth=1, max_entries=1)
    deep_pages, deep_merged = _collect_list_files_pages(tmp_path, max_depth=2, max_entries=1)

    assert all("a/b/" not in page.output for page in shallow_pages)
    assert "a/b/" in "".join(page.output for page in deep_pages)
    assert shallow_merged == ["a/", "root.txt"]
    assert deep_merged == ["a/", "a/b/", "root.txt"]


def test_list_files_char_budget_pages_without_duplicates(tmp_path: Path) -> None:
    expected = [f"{index:03d}-" + ("x" * 180) + ".txt" for index in range(80)]
    for index in range(80):
        (tmp_path / expected[index]).write_text(f"{index}\n", encoding="utf-8")

    first_page = list_files(tmp_path, path=".", max_depth=1, max_entries=200)
    second_page = list_files(
        tmp_path,
        path=".",
        max_depth=1,
        max_entries=200,
        offset=first_page.metadata["next_offset"],
    )

    assert first_page.success is True
    assert first_page.metadata["entries_returned"] < 200
    assert first_page.metadata["has_more"] is True
    assert first_page.metadata["limit_reason"] == "char_limit"
    assert first_page.metadata["next_offset"] == first_page.metadata["entries_returned"]
    assert first_page.metadata["page_output_chars"] <= LIST_FILES_PAGE_MAX_CHARS
    assert second_page.success is True
    assert first_page.output.splitlines() + second_page.output.splitlines() == expected


def test_list_files_three_pages_cover_all_entries(tmp_path: Path) -> None:
    for index in range(450):
        (tmp_path / f"file_{index:04d}.txt").write_text(str(index), encoding="utf-8")

    pages, merged = _collect_list_files_pages(tmp_path, max_depth=1, max_entries=200)

    assert [page.metadata["entries_returned"] for page in pages] == [200, 200, 50]
    assert [page.metadata["has_more"] for page in pages] == [True, True, False]
    assert [page.metadata["next_offset"] for page in pages] == [200, 400, None]
    assert len(merged) == 450
    assert len(set(merged)) == 450
    assert merged == [f"file_{index:04d}.txt" for index in range(450)]


def test_read_file_with_line_numbers(tmp_path: Path) -> None:
    file_path = tmp_path / "example.py"
    file_path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

    result = read_file(tmp_path, "example.py", start_line=2, end_line=3)

    assert result.success is True
    assert result.output.splitlines() == ["   2: b = 2", "   3: c = 3"]
    assert result.output_summary == "Read example.py lines 2-3 of 3."
    assert result.metadata["total_lines"] == 3
    assert result.metadata["end_line"] == 3


def test_read_file_missing(tmp_path: Path) -> None:
    result = read_file(tmp_path, "missing.py")

    assert result.success is False
    assert "does not exist" in result.error


def test_read_file_directory_error(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()

    result = read_file(tmp_path, "pkg")

    assert result.success is False
    assert "directory" in result.error


def test_read_file_invalid_line_range(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("a = 1\n", encoding="utf-8")

    result = read_file(tmp_path, "example.py", start_line=2, end_line=1)

    assert result.success is False
    assert "end_line" in result.error


def test_read_file_path_escape_blocked(tmp_path: Path) -> None:
    result = read_file(tmp_path, "../outside.py")

    assert result.success is False
    assert "escapes repository root" in result.error


def test_read_file_truncates_long_output(tmp_path: Path) -> None:
    content = "\n".join(f"line {index}" for index in range(1, 10))
    (tmp_path / "long.txt").write_text(content, encoding="utf-8")

    result = read_file(tmp_path, "long.txt", start_line=1, end_line=9, max_chars=20)

    assert result.success is True
    assert result.metadata["truncated"] is True
    assert result.output.endswith("... truncated")
