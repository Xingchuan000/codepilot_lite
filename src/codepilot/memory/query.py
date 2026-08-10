from __future__ import annotations

import re

from codepilot.memory.models import MemoryQuery, NormalizedMemoryQuery

_PATH = re.compile(r"(?:^|[\s\"'`(])((?:src|tests?)/[^\s\"'`,;)]+|[\w./-]+\.[A-Za-z0-9]{1,8})")
_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_./-]{2,}")
_STOP_WORDS = {"a", "an", "the", "to", "of", "in", "on", "is", "and", "or"}


def normalize_memory_query(query: MemoryQuery) -> NormalizedMemoryQuery:
    paths = tuple(dict.fromkeys((*query.paths, *(match.group(1) for match in _PATH.finditer(query.text)))))
    words = tuple(dict.fromkeys(word.lower() for word in _WORD.findall(query.text) if word.lower() not in _STOP_WORDS))[:24]
    fragments: list[str] = []
    for chunk in _CJK.findall(query.text):
        fragments.extend([chunk, *(chunk[index : index + 2] for index in range(len(chunk) - 1))])
    return NormalizedMemoryQuery(query.text, words, tuple(dict.fromkeys(fragments))[:24], paths[:16])
