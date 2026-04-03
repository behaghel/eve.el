from __future__ import annotations

import re
from collections.abc import Iterable, Set

DEFAULT_FILLERS = ["um", "uh"]

_WORD_RE = re.compile(r"[^a-z0-9']+")


def normalise_filler(word: str) -> str:
    return _WORD_RE.sub("", word.lower())


def build_filler_set(words: Iterable[str] = DEFAULT_FILLERS) -> frozenset[str]:
    return frozenset(
        normalised for word in words if (normalised := normalise_filler(word))
    )


_DEFAULT_FILLER_SET = build_filler_set()


def is_filler(word: str, fillers: Set[str] = _DEFAULT_FILLER_SET) -> bool:
    normalised = normalise_filler(word)
    return bool(normalised) and normalised in fillers
