"""Rewrites for valid COBOL the grammar rejects; each keeps every line."""

from __future__ import annotations

import re

_I = re.IGNORECASE
# ``IF A NOT= B``: the grammar only tokenises ``NOT =``.
_NOT_EQUAL = re.compile(r"(?<![\w-])NOT= ", _I)
# ``x'00'``: the grammar reads only an upper-case X prefix.
_HEX_PREFIX = re.compile(r"(?<![\w-])x(?=['\"])")
# ``Z'text'``: a Micro Focus null-terminated literal; without the Z it is a plain literal.
# A literal matches first so a Z inside one is kept.
_NULL_TERMINATED_PREFIX = re.compile(r"('[^'\n]*'|\"[^\"\n]*\")|(?<![\w-])z(?=['\"])", _I)
# ``FUNCTION CURRENT-DATE(1:4)``: no reference modification on a function.
_FUNCTION_REFMOD = re.compile(
    r"(FUNCTION\s+[A-Z0-9-]+(?:\s*\([^()]*\))?)(\s*\([^()]*:[^()]*\))", _I
)
# ``ENTRY 'NAME' [USING ...].``, an alternate entry point (IMS ``DLITCBL``).
_ENTRY = re.compile(r"(?<![\w-])ENTRY\s+'[^']*'(?:\s+USING(?:\s+[A-Z0-9-]+)+)?\s*\.", _I)
# A data section with no entries before the next section or division.
_EMPTY_SECTION = re.compile(
    r"(?<![\w-])(?:WORKING-STORAGE|LOCAL-STORAGE|LINKAGE|FILE)\s+SECTION\s*\."
    r"(?=\s*(?:[A-Z-]+\s+SECTION|PROCEDURE\s+DIVISION|\Z))",
    _I,
)
# ``PROGRAM-ID. NAME`` with no closing period, common in IBM samples.
_PROGRAM_ID_NO_PERIOD = re.compile(r"(PROGRAM-ID\.\s*[A-Z0-9][A-Z0-9-]*)(?=[ \t]*\r?\n)", _I)
# Identification paragraphs hold free-form comment entries, possibly empty.
_COMMENT_ENTRY = re.compile(
    r"(?<![\w-])(AUTHOR|INSTALLATION|DATE-WRITTEN|DATE-COMPILED|SECURITY|REMARKS)\s*\..*?"
    r"(?=(?<![\w-])(?:AUTHOR|INSTALLATION|DATE-WRITTEN|DATE-COMPILED|SECURITY|REMARKS)\s*\."
    r"|(?<![\w-])(?:ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION|\Z)",
    _I | re.DOTALL,
)


def rewrite_gaps(code: str) -> str:
    code = _NOT_EQUAL.sub(lambda m: m.group(0)[:3] + " =", code)
    code = _HEX_PREFIX.sub("X", code)
    code = _NULL_TERMINATED_PREFIX.sub(lambda m: m.group(1) or " ", code)
    code = _FUNCTION_REFMOD.sub(lambda m: m.group(1) + _blank(m.group(2)), code)
    code = _ENTRY.sub(lambda m: _blank(m.group(0)), code)
    code = _EMPTY_SECTION.sub(lambda m: _blank(m.group(0)), code)
    code = _PROGRAM_ID_NO_PERIOD.sub(r"\1.", code)
    return _COMMENT_ENTRY.sub(lambda m: _blank(m.group(0)), code)


def _blank(text: str) -> str:
    return re.sub(r"[^\r\n]", " ", text)
