"""Laravel data dialects: Eloquent models, schema migrations, the query builder.

* **Eloquent** (provider): ``protected $table = 'x'``, or Laravel's own
  convention when a model names none, the snake-case plural of the class
  (``GuestlistEntry`` -> ``guestlist_entries``).
* **Migrations** (provider): ``Schema::create('x', ...)`` declares the table,
  ``Schema::table('x', ...)`` evolves it. Both carry ``meta['schema']``, which
  is what marks the repo that owns the table's schema.
* **Query builder** (consumer): ``DB::table('x')``, read as an insert, update
  or delete when the statement chains one, else a select. Raw SQL through
  ``DB::select(...)`` is already the SQL-strings dialect's.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import scan_code

from ..base import ScanContext, line_at
from ..langs import PHP
from .dialect import (
    ALTER_CONFIDENCE,
    CONVENTION_CONFIDENCE,
    EXPLICIT_CONFIDENCE,
    dedup_consumers,
    dedup_emit,
    found_names,
)
from .names import pluralize, snake_case

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract


# Query-builder writes whose name does not start with the SQL verb they run.
_WRITE_ALIASES = {"upsert": "insert", "updateorinsert": "update", "truncate": "delete"}


class EloquentDialect:
    """Eloquent models: the ``$table`` property, else the class-name convention."""

    name = "eloquent"
    extensions = PHP

    _TABLE_RE = re.compile(r"protected\s+\$table\s*=\s*['\"](\w+)['\"]")
    # Only the framework's own bases: a model extending an app base class
    # (`extends BaseModel`) is missed rather than guessed at, and a pivot's
    # default table follows a different rule.
    _MODEL_CLASS_RE = re.compile(
        r"^[ \t]*(?:final[ \t]+)?class[ \t]+(\w+)[ \t]+extends[ \t]+"
        r"\\?(?:[\w\\]*\\)?(?:Model|Authenticatable)\b",
        re.MULTILINE,
    )
    # The file imports Eloquent (or the auth user built on it), so `Model` is
    # that `Model` and not some other library's.
    _ELOQUENT_IMPORT = ("Illuminate\\Database\\Eloquent", "Illuminate\\Foundation\\Auth")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = found_names(self._TABLE_RE, ctx.content, EXPLICIT_CONFIDENCE)
        if not found and any(i in ctx.content for i in self._ELOQUENT_IMPORT):
            found = found_names(
                self._MODEL_CLASS_RE,
                ctx.content,
                CONVENTION_CONFIDENCE,
                lambda cls: pluralize(snake_case(cls)),
            )
        return dedup_emit(ctx, self.name, found)


class LaravelMigrationDialect:
    """``Schema::create`` / ``Schema::table`` in migrations."""

    name = "laravel-migration"
    extensions = PHP

    _HEAD = r"\bSchema::(?:connection\s*\([^)]*\)\s*->\s*)?"
    _CREATE_RE = re.compile(_HEAD + r"create\s*\(\s*['\"]([\w.]+)['\"]")
    _ALTER_RE = re.compile(_HEAD + r"table\s*\(\s*['\"]([\w.]+)['\"]")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if "Schema::" not in ctx.content:
            return []  # far cheaper than the regexes, which have no literal prefix
        created = found_names(self._CREATE_RE, ctx.content, EXPLICIT_CONFIDENCE)
        altered = found_names(self._ALTER_RE, ctx.content, ALTER_CONFIDENCE)
        return dedup_emit(ctx, self.name, created, schema="create") + dedup_emit(
            ctx, self.name, altered, schema="alter"
        )


class LaravelQueryDialect:
    """``DB::table('x')`` query-builder access."""

    name = "laravel-db"
    extensions = PHP

    _TABLE_RE = re.compile(
        r"\bDB::(?:connection\s*\([^)]*\)\s*->\s*)?table\s*\(\s*"
        r"(?P<q>['\"])(?P<table>[\w.]+)(?:\s+as\s+\w+)?(?P=q)"
    )
    # A write the statement chains, first wins; anything else reads.
    _WRITE_RE = re.compile(
        r"->\s*(?P<method>insert\w*|upsert|updateOrInsert|update\w*|increment\w*"
        r"|decrement\w*|delete|truncate)\s*\("
    )

    def _verb(self, content: str, start: int) -> str:
        # Only the statement's own chain counts: a `;` or `->write(` inside a
        # string, an argument or a closure passed to it is not this statement's.
        # The scan starts inside `table(`, so chain level is depth -1.
        write = None
        for i, c, depth in scan_code(content, start, hash_comments=True):
            if depth >= 0:
                continue
            if c == ";":
                break
            if c == "-" and (write := self._WRITE_RE.match(content, i)):
                break
        if write is None:
            return "select"
        method = write.group("method").lower()
        if method in _WRITE_ALIASES:
            return _WRITE_ALIASES[method]
        for verb in ("insert", "update", "delete"):
            if method.startswith(verb):
                return verb
        return "update"  # increment / decrement

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if "DB::" not in ctx.content:
            return []
        found = [
            (m.group("table"), self._verb(ctx.content, m.end()), line_at(ctx.content, m.start()))
            for m in self._TABLE_RE.finditer(ctx.content)
        ]
        return dedup_consumers(ctx, self.name, found)
