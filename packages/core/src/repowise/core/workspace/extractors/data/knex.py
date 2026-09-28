"""Knex: schema builder migrations (providers) and the query builder (consumers).

* **Migrations**: ``knex.schema.createTable('x', ...)`` declares a table and
  ``alterTable('x')`` / ``table('x', ...)`` evolve it, both marked with
  ``meta['schema']`` as the owner's; ``withSchema('s')`` qualifies them.
* **Queries**: ``knex('x')``, ``knex.from('x')`` / ``.table`` / ``.into``, and
  ``knex.select(...).from('x')``, on ``knex`` itself or on a name the file
  binds to a Knex instance (``const db = knex(config)``). The statement's own
  chain says whether it inserts, updates or deletes; anything else reads.

Ceiling: an instance imported from another module under another name
(``import { db } from './db'``) is not followed; the client instances of the
HTTP layer travel between files through the mount pass, and this could too.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from ..calls import Call, call_chain, call_sites, receiver_at
from ..langs import JS_TS
from ..strings import Arg, match_paren
from .dialect import ALTER_CONFIDENCE, EXPLICIT_CONFIDENCE, dedup_consumers, dedup_emit

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_FIRST = Arg(pos=0)
_THIS_RE = re.compile(r"this\s*\.\s*$")

_SCHEMA_OPS = {
    "createTable": "create",
    "createTableIfNotExists": "create",
    "alterTable": "alter",
    "table": "alter",
}
_SCHEMA_CALL = Call(
    re.compile(
        r"\.\s*schema\s*(?:\.\s*withSchema\s*\((?P<schema>[^()]*)\)\s*)?"
        rf"\.\s*(?P<op>{'|'.join(_SCHEMA_OPS)})\s*\("
    ),
    JS_TS,
    (".schema",),
)
# `const db = knex({...})`, `= Knex(config)`, `= require('knex')(config)`.
_INSTANCE_RE = re.compile(
    r"(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*"
    r"""(?:knex|Knex|require\s*\(\s*['"]knex['"]\s*\))\s*\("""
)
_TABLE_METHODS = ("from", "table", "into")
_WRITES = {
    "insert": "insert",
    "upsert": "insert",
    "update": "update",
    "increment": "update",
    "decrement": "update",
    "del": "delete",
    "delete": "delete",
    "truncate": "delete",
}
_ALIAS_RE = re.compile(r"\s+as\s+\w+$", re.IGNORECASE)


@lru_cache(maxsize=64)
def _queries(names: tuple[str, ...]) -> tuple[Call, ...]:
    """``x('t')``, ``x.from('t')`` and ``x.select(...)`` (read on to its ``from``) on *names*."""
    alts = "|".join(re.escape(n) for n in names)
    methods = "|".join((*_TABLE_METHODS, "select", "distinct", "count", "max", "min", "sum", "avg"))
    return (Call(re.compile(rf"(?:{alts})\s*(?:\.\s*(?P<method>{methods})\s*)?\("), JS_TS),)


def _verb(links: list[str]) -> str:
    return next((_WRITES[name] for name in links if name in _WRITES), "select")


class KnexDialect:
    name = "knex"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if "knex" not in content and "Knex" not in content:
            return []
        return self._migrations(ctx) + self._queries(ctx)

    def _migrations(self, ctx: ScanContext) -> list[Contract]:
        found: dict[str, list[tuple[str, float, int]]] = {"create": [], "alter": []}
        for _call, args, m, strings in call_sites(ctx, (_SCHEMA_CALL,)):
            names, refused = strings.resolve(args[:1], _FIRST)
            if refused or not names:
                continue
            table = names[0]
            if m.group("schema") is not None:
                schemas, refused = strings.resolve([m.group("schema")], _FIRST)
                if refused or not schemas:
                    continue
                table = f"{schemas[0]}.{table}"
            kind = _SCHEMA_OPS[m.group("op")]
            confidence = EXPLICIT_CONFIDENCE if kind == "create" else ALTER_CONFIDENCE
            found[kind].append((table, confidence, line_at(ctx.content, m.start())))
        return dedup_emit(ctx, self.name, found["create"], schema="create") + dedup_emit(
            ctx, self.name, found["alter"], schema="alter"
        )

    def _queries(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        names = {"knex", *(m.group("name") for m in _INSTANCE_RE.finditer(content))}
        found: list[tuple[str, str, int]] = []
        for _call, args, m, strings in call_sites(ctx, _queries(tuple(sorted(names)))):
            if not receiver_at(content, m.start(), _THIS_RE):
                continue
            close = match_paren(content, m.end() - 1)
            links = list(call_chain(content, close)) if close >= 0 else []
            method = m.group("method")
            if method is None or method in _TABLE_METHODS:
                table_args = args
            else:
                # `knex.select(...)` names columns; the table is its chain's `from`.
                table_args = next((link.args for link in links if link.name in _TABLE_METHODS), [])
            tables, refused = strings.resolve(table_args[:1], _FIRST)
            if refused or not tables:
                continue
            verb = _verb([link.name for link in links])
            found.append((_ALIAS_RE.sub("", tables[0]), verb, line_at(content, m.start())))
        return dedup_consumers(ctx, self.name, found)
