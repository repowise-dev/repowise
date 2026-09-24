"""Drizzle tables: ``pgTable('users', ...)``, ``mysqlTable``, ``sqliteTable``, ``singlestoreTable``.

The first argument is the table's name. A table declared on a schema object
(``const auth = pgSchema('auth'); auth.table('users', ...)``) is qualified by
it. A table creator (``pgTableCreator(name => `app_${name}`)``) rewrites the
name in a function this does not read, so its tables are not read either.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from ..calls import Call, call_sites, receiver_at
from ..langs import JS_TS
from ..strings import Arg
from .dialect import EXPLICIT_CONFIDENCE, dedup_emit

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_DRIZZLE = "drizzle-orm"
_FIRST = Arg(pos=0)
_TABLE = Call(re.compile(r"(?:pg|mysql|sqlite|singlestore)Table\s*\("), JS_TS)
_SCHEMA = Call(re.compile(r"(?:pg|mysql|singlestore)Schema\s*\("), JS_TS)
# The name a schema object is bound to, read back from the call.
_BINDING_RE = re.compile(r"(?P<name>[A-Za-z_$][\w$]*)\s*=\s*$")


@lru_cache(maxsize=64)
def _schema_tables(names: tuple[str, ...]) -> tuple[Call, ...]:
    alts = "|".join(re.escape(n) for n in names)
    return (Call(re.compile(rf"(?P<schema>{alts})\s*\.\s*table\s*\("), JS_TS),)


class DrizzleDialect:
    name = "drizzle"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if _DRIZZLE not in content:
            return []
        found: list[tuple[str, float, int]] = []
        schemas: dict[str, str] = {}
        for call, args, m, strings in call_sites(ctx, (_TABLE, _SCHEMA)):
            if not receiver_at(content, m.start()):
                continue  # `pgTableCreator`'s product, or a longer name
            names, refused = strings.resolve(args[:1], _FIRST)
            if refused or not names:
                continue
            if call is _SCHEMA:
                bound = _BINDING_RE.search(content, max(0, m.start() - 80), m.start())
                if bound is not None:
                    schemas[bound.group("name")] = names[0]
            else:
                found.append((names[0], EXPLICIT_CONFIDENCE, line_at(content, m.start())))
        if schemas:
            for _call, args, m, strings in call_sites(ctx, _schema_tables(tuple(sorted(schemas)))):
                names, refused = strings.resolve(args[:1], _FIRST)
                if names and not refused and receiver_at(content, m.start()):
                    table = f"{schemas[m.group('schema')]}.{names[0]}"
                    found.append((table, EXPLICIT_CONFIDENCE, line_at(content, m.start())))
        return dedup_emit(ctx, self.name, found)
