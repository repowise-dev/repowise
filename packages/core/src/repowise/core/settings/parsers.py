"""Parsers that give a setting its reader's tolerance for a raw value.

Each turns a raw file or env value into the setting's type the way the code
that reads it does, or raises. Named by ``Setting.parser``; the typed check
(choices, bounds) runs on the result.
"""

from __future__ import annotations

from typing import Any


def folded(raw: Any) -> str | None:
    """Text compared without surrounding space or case (``" Plus "`` is ``plus``).

    Blank text is no value at all, as its readers treat it.
    """
    if not isinstance(raw, str):
        raise ValueError("must be text")
    return raw.strip().lower() or None


def quoted_int(raw: Any) -> int:
    """A whole number, or one written in quotes (``"8000"``)."""
    if isinstance(raw, bool):
        raise ValueError("must be a whole number")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw)
    raise ValueError("must be a whole number")


def int_like(raw: Any) -> int:
    """Anything ``int()`` reads: a number, a numeric string, a float cut down."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("must be a whole number") from None


def whole_count(raw: Any) -> int:
    """A positive number cut to a whole one, at least 1 (``"2.7"`` is 2, ``"0.5"`` is 1)."""
    if isinstance(raw, bool):
        raise ValueError("must be a number")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError("must be a number") from None
    if not number > 0:
        raise ValueError("must be more than 0")
    # Past any bound a setting declares; the bound then clamps it.
    return max(1, int(min(number, 2**31)))


def decisions_mode(raw: Any) -> str:
    """``decisions.mode``: a bare YAML ``off`` arrives as ``False``; ``read-only`` is ``read_only``."""
    if raw is False:
        return "off"
    if not isinstance(raw, str):
        raise ValueError("must be text")
    return raw.replace("-", "_")
