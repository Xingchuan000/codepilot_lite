from codepilot.tools.patch_utils import extract_paths_from_patch, normalize_diff_path


def test_normalize_diff_path_strips_a_prefix() -> None:
    assert normalize_diff_path("a/src/foo.py") == "src/foo.py"


def test_normalize_diff_path_strips_b_prefix() -> None:
    assert normalize_diff_path("b/src/foo.py") == "src/foo.py"


def test_normalize_diff_path_keeps_plain_path() -> None:
    assert normalize_diff_path("src/foo.py") == "src/foo.py"


def test_normalize_diff_path_dev_null_returns_none() -> None:
    assert normalize_diff_path("/dev/null") is None


def test_normalize_diff_path_strips_tab_metadata() -> None:
    assert normalize_diff_path("--- a/src/foo.py\t2026-01-01") == "src/foo.py"


def test_extract_paths_from_single_file_patch() -> None:
    patch = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-old
+new
"""

    assert extract_paths_from_patch(patch) == ["src/a.py"]


def test_extract_paths_from_multi_file_patch() -> None:
    patch = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-old
+new
diff --git a/src/b.py b/src/b.py
--- a/src/b.py
+++ b/src/b.py
@@ -1 +1 @@
-old
+new
"""

    assert extract_paths_from_patch(patch) == ["src/a.py", "src/b.py"]


def test_extract_paths_from_new_file_patch() -> None:
    patch = """diff --git a/src/new.py b/src/new.py
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1 @@
+new
"""

    assert extract_paths_from_patch(patch) == ["src/new.py"]


def test_extract_paths_from_deleted_file_patch() -> None:
    patch = """diff --git a/src/old.py b/src/old.py
--- a/src/old.py
+++ /dev/null
@@ -1 +0,0 @@
-old
"""

    assert extract_paths_from_patch(patch) == ["src/old.py"]


def test_extract_paths_from_rename_fallback_diff_git_header() -> None:
    patch = """diff --git a/src/old.py b/src/new.py
rename from src/old.py
rename to src/new.py
"""

    assert extract_paths_from_patch(patch) == ["src/old.py", "src/new.py"]


def test_extract_paths_deduplicates_preserving_order() -> None:
    patch = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
"""

    assert extract_paths_from_patch(patch) == ["src/a.py"]


def test_extract_paths_from_empty_patch_returns_empty_list() -> None:
    assert extract_paths_from_patch("") == []


def test_extract_paths_from_patch_without_headers_returns_empty_list() -> None:
    assert extract_paths_from_patch("just text\n") == []
