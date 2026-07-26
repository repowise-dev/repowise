"""Small SQL helpers shared by every layer that builds queries by hand.

Kept at the persistence root (rather than inside ``crud``) because the server
routers and MCP tools build their own ``select`` statements and need the same
escaping the CRUD layer uses.
"""

from __future__ import annotations

#: The escape character to pass as ``escape=`` on every ``like``/``ilike``
#: paired with :func:`escape_like`. SQLite has no default LIKE escape
#: character, so it has to be declared on the call for the escaping below to
#: mean anything.
LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters (``%``, ``_``) and the escape char itself.

    Always pair with ``escape=LIKE_ESCAPE`` on the ``like``/``ilike`` call.
    Without this, a value containing ``_`` — which is most Python filenames —
    matches any single character instead of an underscore, so the query
    quietly returns rows the caller never asked for.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
