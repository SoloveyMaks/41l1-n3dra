"""Version comparison for Maven coordinates used by the training snapshot.

The organizers' snapshot is ``numeric-dot-separated``: ``1.9`` equals
``1.9.0`` and ``20230618`` is one numeric component.  Anything containing a
qualifier (``1.0-RC1``, ``2.0+build``) is outside the snapshot and is reported
as an unknown version instead of being guessed.
"""

from __future__ import annotations

import re
from typing import Any

_NUMERIC = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


def parse_version(value: str | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not _NUMERIC.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


def normalize(version: tuple[int, ...]) -> tuple[int, ...]:
    trimmed = list(version)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()
    return tuple(trimmed)


def compare(left: str | tuple[int, ...], right: str | tuple[int, ...]) -> int | None:
    """Return -1/0/1, or None when either side is not a numeric version."""

    lv = parse_version(left) if isinstance(left, str) else left
    rv = parse_version(right) if isinstance(right, str) else right
    if lv is None or rv is None:
        return None
    size = max(len(lv), len(rv))
    padded_l = lv + (0,) * (size - len(lv))
    padded_r = rv + (0,) * (size - len(rv))
    if padded_l < padded_r:
        return -1
    if padded_l > padded_r:
        return 1
    return 0


def in_range(version: str, spec: dict[str, Any]) -> bool | None:
    """Check a numeric version against one advisory range.

    Returns None when the range or the version cannot be compared numerically.
    """

    parsed = parse_version(version)
    if parsed is None:
        return None

    lower = spec.get("lower")
    upper = spec.get("upper")
    lower_inclusive = bool(spec.get("lowerInclusive"))
    upper_inclusive = bool(spec.get("upperInclusive"))

    if lower is not None:
        cmp_lower = compare(parsed, lower)
        if cmp_lower is None:
            return None
        if cmp_lower < 0 or (cmp_lower == 0 and not lower_inclusive):
            return False

    if upper is not None:
        cmp_upper = compare(parsed, upper)
        if cmp_upper is None:
            return None
        if cmp_upper > 0 or (cmp_upper == 0 and not upper_inclusive):
            return False

    return True


def in_any_range(version: str, ranges: list[dict[str, Any]]) -> bool | None:
    unknown = False
    for spec in ranges:
        result = in_range(version, spec)
        if result is True:
            return True
        if result is None:
            unknown = True
    return None if unknown else False


def describe_range(spec: dict[str, Any]) -> str:
    lower = spec.get("lower")
    upper = spec.get("upper")
    left = "[" if spec.get("lowerInclusive") else "("
    right = "]" if spec.get("upperInclusive") else ")"
    if lower is None:
        left = "(-\u221e"
        lower_text = ""
    else:
        lower_text = str(lower)
    upper_text = "" if upper is None else str(upper)
    if lower is None:
        return f"{left}, {upper_text}{right}"
    return f"{left}{lower_text}, {upper_text}{right}"
