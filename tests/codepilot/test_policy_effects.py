from codepilot.policy.effects import classify_shell_command
from codepilot.tools.base import ExternalImpact, Reversibility


def test_read_only_shell_is_local_non_mutating() -> None:
    effect = classify_shell_command("git status")

    assert effect.external_impact == ExternalImpact.NONE
    assert effect.reversibility == Reversibility.NOT_APPLICABLE


def test_pytest_is_local_non_mutating() -> None:
    effect = classify_shell_command("python -m pytest tests -q")

    assert effect.external_impact == ExternalImpact.NONE
    assert effect.reversibility == Reversibility.NOT_APPLICABLE


def test_git_push_is_external_write() -> None:
    effect = classify_shell_command("git push origin feature/foo")

    assert effect.external_impact == ExternalImpact.WRITE
    assert effect.reversibility == Reversibility.UNKNOWN


def test_curl_is_external_read() -> None:
    effect = classify_shell_command("curl https://example.com")

    assert effect.external_impact == ExternalImpact.READ
    assert effect.reversibility == Reversibility.NOT_APPLICABLE


def test_rm_repo_file_is_local_irreversible() -> None:
    effect = classify_shell_command("rm src/obsolete.py")

    assert effect.external_impact == ExternalImpact.NONE
    assert effect.reversibility == Reversibility.IRREVERSIBLE


def test_unknown_python_script_is_unknown() -> None:
    effect = classify_shell_command("python scripts/custom.py")

    assert effect.external_impact == ExternalImpact.UNKNOWN
    assert effect.reversibility == Reversibility.UNKNOWN


def test_complex_shell_is_unknown() -> None:
    effect = classify_shell_command("pytest -q && git push")

    assert effect.external_impact == ExternalImpact.UNKNOWN
    assert effect.reversibility == Reversibility.UNKNOWN


def test_command_substitution_is_unknown() -> None:
    effect = classify_shell_command("cat $(curl https://example.com)")

    assert effect.external_impact == ExternalImpact.UNKNOWN
    assert effect.reversibility == Reversibility.UNKNOWN
