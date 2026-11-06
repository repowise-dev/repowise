"""ORM provider dialects: entity classes that declare (or imply) a table.

The ORM model is the provider side of an app-to-database contract: it declares
which table the repo owns. Consumers of the model are already visible through
the import graph, so this module never scans for model *usage*; it only emits
the declaration.

Explicit names (``__tablename__``, ``@Table(name=...)``,
``self.table_name``, ``db_table``) are extracted verbatim at high confidence.
Convention-derived names (JPA entity class, ActiveRecord class, EF ``DbSet``
property) are emitted at lower confidence: a wrong guess costs a missed link
(the key just never matches), not a false one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import ScanContext, line_at
from ..langs import CSHARP, JAVA, KOTLIN, PYTHON, RUBY
from ..strings import call_arguments
from .dialect import _Found, dedup_emit, found_names
from .names import normalize_table_name, pluralize, snake_case

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

class SqlAlchemyDjangoDialect:
    """Python ORMs and migrations: SQLAlchemy ``__tablename__``, Django
    ``Meta.db_table``, SQLModel ``table=True`` classes (default table = class
    name lower-cased), and Alembic ``op.create_table``."""

    name = "python-orm"
    extensions = PYTHON
    # `__tablename__`, `db_table`, `op.create_table` and `table=True` all hold it.
    requires = ("table",)

    _TABLENAME_RE = re.compile(r"^\s*__tablename__\s*[:=]\s*.*?['\"](\S+?)['\"]", re.MULTILINE)
    _DB_TABLE_RE = re.compile(r"^\s*db_table\s*=\s*['\"](\S+?)['\"]", re.MULTILINE)
    _CREATE_TABLE_RE = re.compile(r"\bop\.create_table\s*\(")
    _SQLMODEL_RE = re.compile(r"\bclass\s+(\w+)\([^)]*\btable\s*=\s*True[^)]*\)")
    _BARE_STRING_RE = re.compile(r"['\"](\w+)['\"]")
    _SCHEMA_KWARG_RE = re.compile(r"schema\s*=\s*['\"](\w+)['\"]")

    def _created_tables(self, content: str) -> list[_Found]:
        r"""The tables each ``op.create_table`` call declares, schema-qualified.

        Alembic takes the target schema as a ``schema=`` keyword that usually
        trails the column list, so the call's first string literal is the bare
        table name and the qualifier has to come from the keyword: a
        schema-qualified migration would otherwise be recorded as ``owners``
        where its SQL consumers say ``core.owners``, and the two keys would
        never meet. The argument list is split at top level
        (:func:`..strings.call_arguments`) so a ``schema=`` belonging to a
        later statement, or one on a nested call inside the argument list,
        cannot lend its schema to this table. The qualified token goes through
        :func:`normalize_table_name` so the case folding - and the default
        schema drop - is the SQL consumer side's own.
        """
        found: list[_Found] = []
        for m in self._CREATE_TABLE_RE.finditer(content):
            args = call_arguments(content, m.end() - 1)  # m.end()-1 is the '('
            if not args:
                continue
            name = self._BARE_STRING_RE.fullmatch(args[0])
            if name is None:
                continue
            table = name.group(1)
            for arg in args[1:]:
                schema = self._SCHEMA_KWARG_RE.fullmatch(arg)
                if schema is not None:
                    table = f"{schema.group(1)}.{table}"
                    break
            raw = normalize_table_name(table)
            if raw is not None:
                found.append((raw, ContractOrigin.DDL, line_at(content, m.start())))
        return found

    def extract(self, ctx: ScanContext) -> list[Contract]:
        # A migration's `create_table` defines the schema; the rest model it.
        # Each pattern runs only where its literal is: the `^\s*` ones have no
        # literal prefix for the regex engine to skip ahead by.
        content = ctx.content
        seen: set[str] = set()
        created = (
            self._created_tables(content)
            if ctx.has_any(("op.create_table",))
            else []
        )
        out = dedup_emit(ctx, self.name, created, schema="create", seen=seen)
        found: list[tuple[str, ContractOrigin, int]] = []
        if ctx.has_any(("__tablename__",)):
            found += found_names(self._TABLENAME_RE, content, ContractOrigin.EXPLICIT_TABLE)
        if ctx.has_any(("db_table",)):
            found += found_names(self._DB_TABLE_RE, content, ContractOrigin.EXPLICIT_TABLE)
        if ctx.has_any(("True",)):
            found += found_names(
                self._SQLMODEL_RE, content, ContractOrigin.NAME_CONVENTION, str.lower
            )
        return out + dedup_emit(ctx, self.name, found, seen=seen)


class JpaDialect:
    """JPA/Jakarta entities: ``@Table(name=...)``, defaulting to the class name."""

    name = "jpa"
    extensions = JAVA | KOTLIN
    requires = ("@Table", "@Entity")

    _TABLE_RE = re.compile(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"")
    # @Entity (optionally parameterized) followed by its class declaration.
    _ENTITY_CLASS_RE = re.compile(
        r"@Entity\b(?:\s*\([^)]*\))?[\s\S]{0,200}?\bclass\s+(\w+)",
    )

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = found_names(self._TABLE_RE, ctx.content, ContractOrigin.EXPLICIT_TABLE)
        if not found:
            # Only fall back to the class-name convention when no explicit
            # @Table names the table (one entity per file is the JPA norm).
            found = found_names(
                self._ENTITY_CLASS_RE, ctx.content, ContractOrigin.NAME_CONVENTION, snake_case
            )
        return dedup_emit(ctx, self.name, found)


class EfCoreDialect:
    """EF Core: ``[Table("x")]`` attributes and ``DbSet<T> Props`` (table = property)."""

    name = "efcore"
    extensions = CSHARP
    requires = ("[Table(", "DbSet<")

    _TABLE_ATTR_RE = re.compile(r"\[Table\(\s*\"([^\"]+)\"")
    _DBSET_RE = re.compile(r"\bDbSet<\s*\w+\s*>\s+(\w+)")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = found_names(self._TABLE_ATTR_RE, ctx.content, ContractOrigin.EXPLICIT_TABLE)
        found += found_names(self._DBSET_RE, ctx.content, ContractOrigin.NAME_CONVENTION)
        return dedup_emit(ctx, self.name, found)


class ActiveRecordDialect:
    """Rails ActiveRecord: explicit ``self.table_name`` or the class convention."""

    name = "activerecord"
    extensions = RUBY
    requires = ("table_name", "ApplicationRecord", "ActiveRecord::Base")

    _TABLE_NAME_RE = re.compile(r"self\.table_name\s*=\s*['\"](\w+)['\"]")
    _MODEL_CLASS_RE = re.compile(
        r"^\s*class\s+(\w+)\s*<\s*(?:ApplicationRecord|ActiveRecord::Base)\b", re.MULTILINE
    )

    def extract(self, ctx: ScanContext) -> list[Contract]:
        found = found_names(self._TABLE_NAME_RE, ctx.content, ContractOrigin.EXPLICIT_TABLE)
        if not found:
            found = found_names(
                self._MODEL_CLASS_RE,
                ctx.content,
                ContractOrigin.NAME_CONVENTION,
                lambda m: pluralize(snake_case(m)),
            )
        return dedup_emit(ctx, self.name, found)
