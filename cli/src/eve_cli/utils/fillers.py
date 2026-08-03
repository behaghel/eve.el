from __future__ import annotations

import re
from collections.abc import Iterable, Sequence, Set

DEFAULT_FILLERS = ["um", "uh"]

_WORD_RE = re.compile(r"[^a-z0-9']+")
_TOKEN_SPLIT_RE = re.compile(r"[a-z0-9']+")


def normalise_filler(word: str) -> str:
    return _WORD_RE.sub("", word.lower())


def normalise_filler_token(token: str) -> str:
    return normalise_filler(token)


def normalise_filler_phrase(phrase: str) -> tuple[str, ...]:
    return tuple(
        normalised
        for token in _TOKEN_SPLIT_RE.findall(phrase.lower())
        if (normalised := normalise_filler_token(token))
    )


def build_filler_set(words: Iterable[str] = DEFAULT_FILLERS) -> frozenset[str]:
    return frozenset(
        normalised for word in words if (normalised := normalise_filler(word))
    )


def build_filler_phrases(
    words: Iterable[str] = DEFAULT_FILLERS,
) -> tuple[tuple[str, ...], ...]:
    phrases = {phrase for word in words if (phrase := normalise_filler_phrase(word))}
    return tuple(sorted(phrases, key=lambda phrase: (-len(phrase), phrase)))


_DEFAULT_FILLER_SET = build_filler_set()


def is_filler(word: str, fillers: Set[str] = _DEFAULT_FILLER_SET) -> bool:
    normalised = normalise_filler(word)
    return bool(normalised) and normalised in fillers


def phrase_matches(
    tokens: Sequence[str], phrase: Sequence[str], start_index: int
) -> bool:
    end_index = start_index + len(phrase)
    return end_index <= len(tokens) and tuple(tokens[start_index:end_index]) == tuple(
        phrase
    )
