"""Display-only French elision spacing; source token records stay independent."""

from __future__ import annotations

import re
from collections.abc import Iterable

_ELISION = re.compile(
    r"(?<![\w'’])"
    r"(c|d|j|l|m|n|s|t|qu|jusqu|lorsqu|puisqu|quoiqu|presqu|quelqu|entr|aujourd)"
    r"(['’]?)$",
    re.IGNORECASE,
)


def display_separator(left: str, right: str) -> str:
    """Join French elision fragments, not arbitrary opening quotation marks."""
    if not left or not right:
        return ""
    match = _ELISION.search(left)
    if match:
        if match[2] and right[0].isalpha():
            return ""
        if not match[2] and right[0] in "'’":
            if len(right) == 1 or right[1].isalpha():
                return ""
    return " "


def join_display_tokens(tokens: Iterable[str]) -> str:
    """Project token fragments as typography without altering their contents."""
    text = ""
    for token in tokens:
        if token:
            text += display_separator(text, token) + token
    return text
