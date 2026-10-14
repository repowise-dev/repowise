"""The one content hash shared by capture, commit binding and transcript backfill.

A line is normalised before hashing so the same text hashes the same whichever
tool wrote it: a trailing ``\\r`` (CRLF checkouts, Windows agents) and trailing
whitespace are dropped, leading indentation is kept because it is content.

Only hashes leave this module; the text never does.
"""

from __future__ import annotations

import hashlib
import re

#: A line needs this many non-space characters, one of them alphanumeric, to
#: count toward a binding. Blank, brace-only and short lines (``}``, ``pass``,
#: ``return x``) occur everywhere, so they can ride inside a bound run but never
#: anchor one.
MIN_SUBSTANTIVE_CHARS = 10

#: Stub statements long enough to pass the length rule but written the same way
#: everywhere (Python, JS, TS), so they never anchor a run either.
_STUB_LINE = re.compile(
    r"(?:return\s+(?:none|null|undefined|true|false)"
    r"|raise\s+NotImplementedError(?:\(.*\))?"
    r"|throw\s+new\s+Error\(\s*(['\"`])not\s+implemented\.?\1\s*\))\s*;?",
    re.IGNORECASE,
)


def normalize_line(line: str) -> str:
    return line.rstrip("\r\n").rstrip()


def split_lines(text: str) -> list[str]:
    """*text* as normalised lines, split on ``\\n`` only, as git does.

    ``str.splitlines`` would also split on form feed and U+2028, lines git
    keeps whole, so the hashes would never meet the diff's.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [normalize_line(line) for line in lines]


def line_hash(line: str) -> str:
    """16 hex chars of BLAKE2b over the normalised line."""
    return hashlib.blake2b(normalize_line(line).encode("utf-8"), digest_size=8).hexdigest()


def is_substantive(line: str) -> bool:
    stripped = normalize_line(line).strip()
    return (
        len(stripped) >= MIN_SUBSTANTIVE_CHARS
        and any(ch.isalnum() for ch in stripped)
        and not _STUB_LINE.fullmatch(stripped)
    )


def opaque_id(value: str) -> str:
    """A session id as stored: 16 hex chars of SHA-256, never the raw id."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
