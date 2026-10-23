"""The text the COBOL grammar parses, with every line kept at its number."""

from __future__ import annotations

from .fragments import is_fragment, is_procedure_fragment, wrap_fragment
from .grammar_gaps import rewrite_gaps
from .reference_format import is_free_format, normalize_fixed_format
from .statements import mask_statements

# A lossless round trip for bytes that are not UTF-8 (EBCDIC-converted Latin-1).
_ENCODING, _ERRORS = "utf-8", "surrogateescape"


def code_view(source: bytes) -> str:
    """*source* as program text only: comments, sequence area and columns 73+ blank."""
    text = source.decode(_ENCODING, _ERRORS)
    # Free format keeps its text as written; only fixed format has columns.
    return text if is_free_format(text) else normalize_fixed_format(text)


def prepare_cobol_source(source: bytes) -> bytes:
    """The parse input: a code view with EXEC/COPY masked, copybooks wrapped."""
    code = code_view(source)
    if is_fragment(code):
        procedure = is_procedure_fragment(code)
        code = wrap_fragment(mask_statements(code, procedure_from=0 if procedure else None))
    else:
        code = mask_statements(code)
    return rewrite_gaps(code).encode(_ENCODING, _ERRORS)
