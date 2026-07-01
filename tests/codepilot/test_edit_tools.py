from pathlib import Path
import subprocess

from codepilot.tools.edit_tools import apply_patch, replace_range


def test_replace_range_replaces_single_line(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\nb\nc\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 2, 2, "x\n")

    assert result.success is True
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "a\nx\nc\n"


def test_replace_range_replaces_multiple_lines(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\nb\nc\nd\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 2, 3, "x\ny\n")

    assert result.success is True
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "a\nx\ny\nd\n"


def test_replace_range_deletes_range_when_replacement_empty(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\nb\nc\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 2, 2, "")

    assert result.success is True
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "a\nc\n"


def test_replace_range_dry_run_does_not_write_file(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\nb\nc\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 2, 2, "x\n", dry_run=True)

    assert result.success is True
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "a\nb\nc\n"


def test_replace_range_returns_diff_preview(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\nb\nc\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 2, 2, "x\n")

    assert result.success is True
    assert result.output.startswith("--- a/sample.py")


def test_replace_range_invalid_start_line_fails(tmp_path: Path) -> None:
    result = replace_range(tmp_path, "sample.py", 0, 1, "x\n")

    assert result.success is False
    assert result.error == "start_line must be >= 1"


def test_replace_range_end_before_start_fails(tmp_path: Path) -> None:
    result = replace_range(tmp_path, "sample.py", 2, 1, "x\n")

    assert result.success is False
    assert result.error == "end_line must be >= start_line"


def test_replace_range_start_line_out_of_range_fails(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 2, 2, "x\n")

    assert result.success is False
    assert result.error == "start_line exceeds total lines: 1"


def test_replace_range_end_line_out_of_range_fails(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 1, 2, "x\n")

    assert result.success is False
    assert result.error == "end_line exceeds total lines: 1"


def test_replace_range_missing_file_fails(tmp_path: Path) -> None:
    result = replace_range(tmp_path, "missing.py", 1, 1, "x\n")

    assert result.success is False
    assert result.error == "File does not exist: missing.py"


def test_replace_range_directory_fails(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()

    result = replace_range(tmp_path, "pkg", 1, 1, "x\n")

    assert result.success is False
    assert result.error == "Path is a directory: pkg"


def test_replace_range_path_escape_fails(tmp_path: Path) -> None:
    result = replace_range(tmp_path, "../outside.py", 1, 1, "x\n")

    assert result.success is False
    assert "Path escapes repository root" in result.error


def test_replace_range_metadata_contains_required_fields(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text("a\nb\n", encoding="utf-8")

    result = replace_range(tmp_path, "sample.py", 1, 1, "x\n", dry_run=True)

    assert result.success is True
    assert result.metadata["path"] == "sample.py"
    assert result.metadata["start_line"] == 1
    assert result.metadata["end_line"] == 1
    assert result.metadata["total_lines"] == 2
    assert result.metadata["replacement_lines"] == 1
    assert result.metadata["dry_run"] is True
    assert result.metadata["risk"] == "local_write"


def test_apply_patch_applies_normal_patch(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("old = 1\nprint(old)\n", encoding="utf-8")
    patch = """diff --git a/src/demo.py b/src/demo.py
--- a/src/demo.py
+++ b/src/demo.py
@@ -1,2 +1,2 @@
-old = 1
+old = 2
 print(old)
"""

    result = apply_patch(tmp_path, patch)

    assert result.success is True
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "old = 2\nprint(old)\n"


def test_apply_patch_adds_new_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    patch = """diff --git a/src/new.py b/src/new.py
new file mode 100644
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+print("new")
"""

    result = apply_patch(tmp_path, patch)

    assert result.success is True
    assert (tmp_path / "src" / "new.py").read_text(encoding="utf-8") == 'print("new")\n'


def test_apply_patch_deletes_file(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.py").write_text("old\n", encoding="utf-8")
    patch = """diff --git a/src/old.py b/src/old.py
--- a/src/old.py
+++ /dev/null
@@ -1 +0,0 @@
-old
"""

    result = apply_patch(tmp_path, patch)

    assert result.success is True
    assert not (tmp_path / "src" / "old.py").exists()


def test_apply_patch_dry_run_checks_without_writing(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    patch = """diff --git a/src/demo.py b/src/demo.py
--- a/src/demo.py
+++ b/src/demo.py
@@ -1 +1 @@
-old
+new
"""

    result = apply_patch(tmp_path, patch, dry_run=True)

    assert result.success is True
    assert (tmp_path / "src" / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_apply_patch_empty_patch_fails(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)

    result = apply_patch(tmp_path, "")

    assert result.success is False
    assert result.error == "patch must not be empty"


def test_apply_patch_without_touched_paths_fails(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)

    result = apply_patch(tmp_path, "just text\n")

    assert result.success is False
    assert result.error == "Patch does not contain extractable file paths."


def test_apply_patch_context_mismatch_fails_with_suggestion(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("actual\n", encoding="utf-8")
    patch = """diff --git a/src/demo.py b/src/demo.py
--- a/src/demo.py
+++ b/src/demo.py
@@ -1 +1 @@
-expected
+new
"""

    result = apply_patch(tmp_path, patch)

    assert result.success is False
    assert "Patch check failed" in result.error
    assert result.metadata["suggestion"] == "Read the latest file content and regenerate a smaller patch."


def test_apply_patch_missing_repo_fails(tmp_path: Path) -> None:
    result = apply_patch(tmp_path / "missing", "diff --git a/a b/a\n")

    assert result.success is False
    assert result.error.startswith("Repo does not exist:")


def test_apply_patch_repo_is_file_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo.txt"
    repo.write_text("x\n", encoding="utf-8")

    result = apply_patch(repo, "diff --git a/a b/a\n")

    assert result.success is False
    assert result.error.startswith("Repo is not a directory:")


def test_apply_patch_metadata_contains_required_fields(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    patch = """diff --git a/src/demo.py b/src/demo.py
--- a/src/demo.py
+++ b/src/demo.py
@@ -1 +1 @@
-old
+new
"""

    result = apply_patch(tmp_path, patch, dry_run=True)

    assert result.success is True
    assert result.metadata["touched_paths"] == ["src/demo.py"]
    assert result.metadata["dry_run"] is True
    assert result.metadata["check_returncode"] == 0
    assert result.metadata["apply_returncode"] is None
    assert result.metadata["risk"] == "local_write"
