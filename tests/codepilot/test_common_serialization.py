from dataclasses import dataclass
from pathlib import Path

from codepilot.common.serialization import to_jsonable


@dataclass(frozen=True)
class Sample:
    path: Path
    tags: set[str]


def test_to_jsonable_converts_nested_values() -> None:
    result = to_jsonable({"sample": Sample(Path("a.txt"), {"b", "a"})})

    assert result == {"sample": {"path": "a.txt", "tags": ["a", "b"]}}
