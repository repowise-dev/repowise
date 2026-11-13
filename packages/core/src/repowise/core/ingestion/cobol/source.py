"""The text the COBOL grammar parses, with every line kept at its number."""

from __future__ import annotations

import re

from .fragments import is_fragment, is_procedure_fragment, wrap_fragment
from .grammar_gaps import rewrite_gaps
from .reference_format import is_free_format, normalize_fixed_format, normalize_free_format
from .statements import mask_statements

# A lossless round trip for bytes that are not UTF-8 (EBCDIC-converted Latin-1).
_ENCODING, _ERRORS = "utf-8", "surrogateescape"
# A ``*>`` comment to the end of its line; a literal matches first so its text is kept.
_INLINE_COMMENT = re.compile(r"('[^'\n]*'|\"[^\"\n]*\")|\*>[^\r\n]*")


def code_view(source: bytes) -> str:
    """*source* as program text only: comments, sequence area and columns 73+ blank."""
    text = source.decode(_ENCODING, _ERRORS)
    code = normalize_free_format(text) if is_free_format(text) else normalize_fixed_format(text)
    return _INLINE_COMMENT.sub(lambda m: m.group(1) or " " * len(m.group(0)), code)


# DCLGEN output for COBOL: the table declaration, then a level-numbered record.
# A PL/I member declares ``DCL 1 NAME,`` and a VMS DCL procedure has neither.
_SQL_DECLARE_TABLE = re.compile(r"\bEXEC\s+SQL\s+DECLARE\s+[\w.$#@-]+\s+TABLE\b", re.I)
_DATA_ENTRY = re.compile(r"^[ \t]*\d{1,2}[ \t]+[A-Z0-9][A-Z0-9-]*[ \t]*\.", re.I | re.M)


def is_cobol_dclgen(source: bytes) -> bool:
    """Whether a ``.dcl`` member is a DCLGEN copybook written for COBOL."""
    code = code_view(source)
    return _SQL_DECLARE_TABLE.search(code) is not None and _DATA_ENTRY.search(code) is not None


def prepare_cobol_source(source: bytes) -> bytes:
    """The parse input: a code view with EXEC/COPY masked, copybooks wrapped."""
    code = code_view(source)
    if is_fragment(code):
        procedure = is_procedure_fragment(code)
        code = wrap_fragment(mask_statements(code, procedure_from=0 if procedure else None))
    else:
        code = mask_statements(code)
    return rewrite_gaps(code).encode(_ENCODING, _ERRORS)
