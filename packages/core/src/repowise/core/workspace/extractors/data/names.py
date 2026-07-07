"""Table-name normalization shared by every data dialect.

All the false links in table matching come from name mismatches: quoting
(``"users"`` / `` `users` `` / ``[users]``), schema prefixes
(``public.users`` vs ``users``), and case (``Users`` vs ``users``). Every
dialect funnels raw table tokens through :func:`normalize_table_name` so the
rules live in one place and providers/consumers meet on the same key.
"""

from __future__ import annotations

import re

# A normalized table name: starts with a letter/underscore, then word chars
# (``$`` appears in Oracle/T-SQL identifiers). Anything else after stripping
# quotes is not a concrete table token (interpolation residue, operators).
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_$]*$")

# Schema names that carry no ownership signal: every Postgres table lives in
# ``public`` and every T-SQL table in ``dbo`` unless told otherwise, so a
# provider that writes the prefix and a consumer that omits it must still meet.
# Named schemas (``analytics.events``) are kept: they disambiguate for real.
_DEFAULT_SCHEMAS = frozenset({"public", "dbo"})

_QUOTE_CHARS = '"`'


def _strip_quotes(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == "[" and token[-1] == "]":
        return token[1:-1]
    if len(token) >= 2 and token[0] == token[-1] and token[0] in _QUOTE_CHARS:
        return token[1:-1]
    return token


def split_qualified(raw: str) -> tuple[str | None, str]:
    """Split ``schema.table`` into ``(schema, table)``, unquoting each part.

    A three-part name (``db.schema.table``) keeps only the last two parts;
    the database qualifier never disambiguates within one workspace.
    """
    parts = [_strip_quotes(p) for p in raw.split(".")]
    if len(parts) >= 2:
        return parts[-2] or None, parts[-1]
    return None, parts[0]


def normalize_table_name(raw: str) -> str | None:
    """Reduce a raw table token to its canonical match key, or ``None``.

    Lower-cases, strips quoting and default-schema prefixes, and keeps a named
    schema as ``schema.table``. Returns ``None`` for anything that is not a
    concrete identifier (interpolated fragments, temp tables, empty tokens) -
    a ``None`` here means "emit no contract", never "guess".
    """
    if not raw:
        return None
    schema, table = split_qualified(raw)
    table = table.lower().strip()
    if not table or table.startswith("#") or not _IDENT_RE.match(table):
        return None
    if schema:
        schema = schema.lower().strip()
        if schema in _DEFAULT_SCHEMAS or not _IDENT_RE.match(schema):
            schema = None
    return f"{schema}.{table}" if schema else table
