"""Known display layout, with conservative legacy elision fallback."""

from __future__ import annotations

import re
from collections.abc import Iterable

_ELISION = re.compile(
    r"(?<![\w'’])"
    r"(c|d|j|l|m|n|s|t|qu|jusqu|lorsqu|puisqu|quoiqu|presqu|quelqu|entr|aujourd)"
    r"(['’]?)$",
    re.IGNORECASE,
)


def valid_separator(value: object) -> bool:
    """Layout contains only horizontal whitespace, or an explicit empty join."""
    return isinstance(value, str) and all(
        char.isspace() and char not in "\r\n\v\f\x85\u2028\u2029" for char in value
    )


def literal_separators(text: str, fragments: Iterable[str]) -> list[str] | None:
    """Map exact fragments to known layout; never align or rewrite lexical text."""
    separators: list[str] = []
    cursor = 0
    for fragment in fragments:
        start = cursor
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        separator = text[start:cursor]
        if not valid_separator(separator) or not text.startswith(fragment, cursor):
            return None
        separators.append(separator)
        cursor += len(fragment)
    return separators if not text[cursor:].strip() else None


def display_separator(
    left: str, right: str, separator_before: str | None = None
) -> str:
    """Prefer explicit layout; otherwise retain conservative legacy behavior."""
    if not left or not right:
        return ""
    if separator_before is not None:
        return separator_before
    match = _ELISION.search(left)
    if match:
        if match[2] and right[0].isalpha():
            return ""
        if not match[2] and right[0] in "'’":
            if len(right) == 1 or right[1].isalpha():
                return ""
    return " "


def join_display_tokens(
    tokens: Iterable[str], *, separators: Iterable[str | None] | None = None
) -> str:
    """Project timed fragments without altering their contents or identity."""
    fragments = list(tokens)
    layout = list(separators) if separators is not None else [None] * len(fragments)
    text = ""
    for token, separator in zip(fragments, layout, strict=True):
        if token:
            text += display_separator(text, token, separator) + token
    return text
