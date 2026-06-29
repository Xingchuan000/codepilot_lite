from pathlib import Path

from codepilot.tools.base import ToolRisk
from codepilot.tools.file_tools import list_files, read_file


def test_list_files_basic(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("assert True\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=2)

    assert result.success is True
    assert result.output.splitlines() == ["src/", "src/main.py", "tests/", "tests/test_main.py"]
    assert result.output_summary == "Listed 4 entries under ."
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


def test_list_files_truncates_max_entries(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"file_{index}.txt").write_text(f"{index}\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=1, max_entries=3)

    assert result.success is True
    assert len(result.output.splitlines()) == 3
    assert result.metadata["truncated"] is True
    assert result.output_summary.endswith("output truncated.")


def test_list_files_respects_max_depth_exactly(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.py").write_text("x\n", encoding="utf-8")

    result = list_files(tmp_path, path=".", max_depth=2)

    assert result.success is True
    assert "a/" in result.output
    assert "a/b/" in result.output
    assert "a/b/c.py" not in result.output

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
