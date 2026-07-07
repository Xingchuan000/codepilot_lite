from __future__ import annotations

from pathlib import Path

from codepilot.tui.redaction import redact_string, redact_value, relative_path_for_display, relative_paths_in_text, truncate_text


def test_redact_value_masks_sensitive_mapping_keys() -> None:
    payload = redact_value(
        {
            "token": "abc",
            "password": "abc",
            "api_key": "abc",
            "authorization": "abc",
            "cookie": "abc",
            "private_key": "abc",
            "env": {"nested": "abc"},
        }
    )

    assert payload["token"] == "[REDACTED]"
    assert payload["env"] == "[REDACTED]"


def test_redact_value_recurses_into_nested_collections() -> None:
    payload = redact_value(({"items": ["Bearer secret", {"password": "x"}]},))

    assert payload[0]["items"][1]["password"] == "[REDACTED]"
    assert "[REDACTED]" in str(payload)


def test_redact_string_masks_known_token_patterns() -> None:
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz github_pat_123 ghp_123 gho_123 ghs_123 sk-abcdefghijklmnopqrstuvwxyz -----BEGIN PRIVATE KEY-----abc-----END PRIVATE KEY-----"

    redacted = redact_string(text)

    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "[REDACTED]" in redacted


def test_truncate_text_adds_marker() -> None:
    text = truncate_text("a" * 600, max_chars=50)

    assert text.endswith("... truncated")


def test_relative_path_for_display_hides_absolute_path_by_default(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"

    assert relative_path_for_display(path) == "demo.txt"
    assert relative_path_for_display(path, base_dir=tmp_path) == "demo.txt"


def test_relative_paths_in_text_converts_inner_absolute_paths(tmp_path: Path) -> None:
    base_dir = tmp_path / "repo"
    inside_path = base_dir / "src" / "a.py"
    outside_path = Path("/etc/passwd")

    text = relative_paths_in_text(f"changed {inside_path} and {outside_path}", base_dir=base_dir)

    assert "src/a.py" in text
    assert "passwd" in text
    assert str(base_dir) not in text


def test_relative_paths_in_text_skips_urls_and_plain_text(tmp_path: Path) -> None:
    base_dir = tmp_path / "repo"
    text = relative_paths_in_text("visit https://example.com/a/b or use mcp.filesystem.read_file", base_dir=base_dir)

    assert text == "visit https://example.com/a/b or use mcp.filesystem.read_file"


def test_relative_paths_in_text_handles_multiple_paths(tmp_path: Path) -> None:
    base_dir = tmp_path / "repo"
    text = relative_paths_in_text(
        f"compare {base_dir / 'src' / 'one.py'} with {base_dir / 'tests' / 'two.py'}",
        base_dir=base_dir,
    )

    assert "src/one.py" in text
    assert "tests/two.py" in text
