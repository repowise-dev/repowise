"""Which public symbols are API a caller reaches for.

One rule, read by the file page's API list, the module digest's concept index
and spotlight selection, so a symbol never gets a page of its own while its
file page files it under "also defined". A leaf module: the selection and page
generator packages import each other's neighbours, and both import this.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from repowise.core.ingestion.lang_helpers.anonymous_classes import is_anonymous_scope


def is_public_api(symbol: Mapping[str, Any]) -> bool:
    """Whether a public symbol is API a caller would reach for.

    Two kinds of name are public to the parser and are not an API. A dunder is
    the language talking to itself, and ``__all__`` in particular is metadata
    *about* the API rather than part of it. A module-level ``variable`` with no
    capital in its name is module state — ``log``, ``logger``, ``router``, and
    Alembic's ``revision`` / ``down_revision`` are 771 of the 878 such symbols
    in this repository, and not one of them is something another file calls.
    The type aliases that share the kind (``SourceFile``, ``ReasoningMode``)
    keep their capitals and stay. A member of an anonymous class
    (``Gson::make::Comparator``) is reached through its supertype, never by
    name, and has no type of its own to be listed under.
    """
    name = str(symbol.get("name") or "")
    if name.startswith("__") and name.endswith("__"):
        return False
    if is_anonymous_scope(symbol.get("language"), symbol.get("parent_name")):
        return False
    if symbol.get("kind") == "variable" and not symbol.get("parent_name"):
        return any(c.isupper() for c in name)
    return True
