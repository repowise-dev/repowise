"""ORM provider dialects: entity classes that declare (or imply) a table.

The ORM model is the provider side of an app-to-database contract: it declares
which table the repo owns. Consumers of the model are already visible through
the import graph, so this module never scans for model *usage*; it only emits
the declaration.

Explicit names (``__tablename__``, ``@Table(name=...)``, ``$table``,
``self.table_name``, ``db_table``) are extracted verbatim at high confidence.
Convention-derived names (JPA entity class, ActiveRecord class, EF ``DbSet``
property) are emitted at lower confidence: a wrong guess costs a missed link
(the key just never matches), not a false one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from ..langs import CSHARP, JAVA, KOTLIN, PHP, PYTHON, RUBY
from .dialect import build_table_provider

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_EXPLICIT_CONFIDENCE = 0.85
_CONVENTION_CONFIDENCE = 0.6


def _snake_case(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _pluralize(name: str) -> str:
    """Naive English pluralization, matching the common ActiveRecord cases.

    Deliberate ceiling: irregular nouns (person/people) are wrong here and
    yield a missed link, not a false one. Upgrade path: vendor a real
    inflector if Rails smoke tests show it matters.
    """
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    return name + "s"


#: One table declaration: ``(raw_name, confidence, line)``.
_Found = tuple[str, float, int]


def _found(
    pattern: re.Pattern[str],
    content: str,
    confidence: float,
    transform: Callable[[str], str] = str,
) -> list[_Found]:
    r"""Every match of *pattern*'s first group, with the line it sits on.

    The line comes from the group, not the match: three of these patterns open
    with ``^\s*`` under ``MULTILINE``, where ``^`` matches at a preceding blank
    line and ``\s*`` eats the newlines, putting ``m.start()`` above the
    declaration.
    """
    return [
        (transform(m.group(1)), confidence, line_at(content, m.start(1)))
        for m in pattern.finditer(content)
    ]


def _dedup_emit(
    ctx: ScanContext,
    framework: str,
    found: list[_Found],
) -> list[Contract]:
    out: list[Contract] = []
    seen: set[str] = set()
    for raw, confidence, line in found:
        if raw in seen:
            continue
        seen.add(raw)
        contract = build_table_provider(
            ctx, table_raw=raw, framework=framework, line=line, confidence=confidence
        )
        if contract is not None:
            out.append(contract)
    return out


class SqlAlchemyDjangoDialect:
    """Python ORMs and migrations: SQLAlchemy ``__tablename__``, Django
    ``Meta.db_table``, SQLModel ``table=True`` classes (default table = class
    name lower-cased), and Alembic ``op.create_table``."""

    name = "python-orm"
    extensions = PYTHON

    _TABLENAME_RE = re.compile(r"^\s*__tablename__\s*[:=]\s*.*?['\"](\S+?)['\"]", re.MULTILINE)
    _DB_TABLE_RE = re.compile(r"^\s*db_table\s*=\s*['\"](\S+?)['\"]", re.MULTILINE)
    _CREATE_TABLE_RE = re.compile(r"\bop\.create_table\(\s*['\"](\w+)['\"]")
    _SQLMODEL_RE = re.compile(r"\bclass\s+(\w+)\([^)]*\btable\s*=\s*True[^)]*\)")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = _found(self._TABLENAME_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        found += _found(self._DB_TABLE_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        found += _found(self._CREATE_TABLE_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        found += _found(self._SQLMODEL_RE, ctx.content, _CONVENTION_CONFIDENCE, str.lower)
        return _dedup_emit(ctx, self.name, found)


class JpaDialect:
    """JPA/Jakarta entities: ``@Table(name=...)``, defaulting to the class name."""

    name = "jpa"
    extensions = JAVA | KOTLIN

    _TABLE_RE = re.compile(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"")
    # @Entity (optionally parameterized) followed by its class declaration.
    _ENTITY_CLASS_RE = re.compile(
        r"@Entity\b(?:\s*\([^)]*\))?[\s\S]{0,200}?\bclass\s+(\w+)",
    )

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = _found(self._TABLE_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        if not found:
            # Only fall back to the class-name convention when no explicit
            # @Table names the table (one entity per file is the JPA norm).
            found = _found(
                self._ENTITY_CLASS_RE, ctx.content, _CONVENTION_CONFIDENCE, _snake_case
            )
        return _dedup_emit(ctx, self.name, found)


class EfCoreDialect:
    """EF Core: ``[Table("x")]`` attributes and ``DbSet<T> Props`` (table = property)."""

    name = "efcore"
    extensions = CSHARP

    _TABLE_ATTR_RE = re.compile(r"\[Table\(\s*\"([^\"]+)\"")
    _DBSET_RE = re.compile(r"\bDbSet<\s*\w+\s*>\s+(\w+)")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = _found(self._TABLE_ATTR_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        found += _found(self._DBSET_RE, ctx.content, _CONVENTION_CONFIDENCE)
        return _dedup_emit(ctx, self.name, found)


class ActiveRecordDialect:
    """Rails ActiveRecord: explicit ``self.table_name`` or the class convention."""

    name = "activerecord"
    extensions = RUBY

    _TABLE_NAME_RE = re.compile(r"self\.table_name\s*=\s*['\"](\w+)['\"]")
    _MODEL_CLASS_RE = re.compile(
        r"^\s*class\s+(\w+)\s*<\s*(?:ApplicationRecord|ActiveRecord::Base)\b", re.MULTILINE
    )

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = _found(self._TABLE_NAME_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        if not found:
            found = _found(
                self._MODEL_CLASS_RE,
                ctx.content,
                _CONVENTION_CONFIDENCE,
                lambda m: _pluralize(_snake_case(m)),
            )
        return _dedup_emit(ctx, self.name, found)


class EloquentDialect:
    """Laravel Eloquent: ``protected $table = '...'``."""

    name = "eloquent"
    extensions = PHP

    _TABLE_RE = re.compile(r"protected\s+\$table\s*=\s*['\"](\w+)['\"]")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = _found(self._TABLE_RE, ctx.content, _EXPLICIT_CONFIDENCE)
        return _dedup_emit(ctx, self.name, found)
