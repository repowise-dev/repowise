"""Parse repo-owned Python source without leaking its warnings to the console.

An invalid escape sequence is a SyntaxWarning from Python 3.12 and a
DeprecationWarning before it, so both are silenced.
"""

from __future__ import annotations

import ast
import symtable
import warnings
from typing import Any


def parse_python(source: str | bytes, **kwargs: Any) -> ast.Module:
    """``ast.parse`` that stays quiet about warnings in the analysed repo's own code."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(source, **kwargs)


def symtable_python(source: str) -> symtable.SymbolTable:
    """Module symbol table of *source*, quiet like ``parse_python``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return symtable.symtable(source, "<module>", "exec")
