"""Consumer dialect: SQL string literals embedded in application code.

Precision rules (this is where the false links would come from):

1. Only *string literals* are considered, and only ones that contain a SQL
   verb; a prose comment mentioning "from users" never matches because it is
   not inside a string carrying a verb shape.
2. When the literal parses under sqlglot, table names come from the AST (the
   high-precision path). When parsing fails (interpolation placeholders break
   the grammar), a verb-anchored regex extracts the table token instead.
3. A table token that is itself interpolated (``FROM {table}`` /
   ``FROM ${table}`` / ``FROM " + name``) is skipped: normalization rejects
   non-identifier tokens, so an interpolated target emits nothing.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from ..langs import CSHARP, GO, JAVA, JS_TS, KOTLIN, PHP, PYTHON, RUBY
from .dialect import build_table_consumer

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

# String-literal shapes as ONE left-to-right alternation (longest delimiter
# first within the alternation). A single scan matters: whichever literal
# *starts* first consumes its full span, so a double-quoted fragment inside a
# single-quoted SQL string (``'... JOIN "user" u ...'``) stays part of that
# string instead of being lexed as its own literal. Template literals and
# verbatim strings are included; the interpolation guard (rule 3) handles
# their placeholders.
_STRING_RE = re.compile(
    r'"""(?P<t1>.*?)"""'
    r"|'''(?P<t2>.*?)'''"
    r"|`(?P<bt>[^`]*)`"
    r'|"(?P<dq>(?:[^"\\\n]|\\.)*)"'
    r"|'(?P<sq>(?:[^'\\\n]|\\.)*)'",
    re.DOTALL,
)

# A literal must contain one of these verb shapes to be treated as SQL at all.
# ``UPDATE`` alone matches prose ("update the settings"), so it requires its
# ``SET`` companion; ``SELECT`` requires ``FROM``.
_SQL_VERB_RE = re.compile(
    r"\bSELECT\s[\s\S]+?\sFROM\s"
    r"|\bINSERT\s+INTO\s"
    r"|\bUPDATE\s+\S+\s+SET\s"
    r"|\bDELETE\s+FROM\s"
    r"|\bMERGE\s+INTO\s",
    re.IGNORECASE,
)

# English prose can still satisfy the verb shape ("Select the id from users").
# Real SQL in code is either upper-cased or carries SQL punctuation; prose is
# neither. One of the two must hold for the literal to count.
_UPPER_VERB_RE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|MERGE)\b")
_SQL_PUNCT_RE = re.compile(r"[*=;()]")

# Verb-anchored table extraction for the regex fallback path. The token class
# excludes interpolation openers so ``FROM {table}`` captures nothing.
_TABLE_TOKEN = r'([A-Za-z_"\[`][\w."\[\]`$]*)'
_VERB_TABLE_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("select", re.compile(r"\bFROM\s+" + _TABLE_TOKEN, re.IGNORECASE)),
    ("join", re.compile(r"\bJOIN\s+" + _TABLE_TOKEN, re.IGNORECASE)),
    ("insert", re.compile(r"\bINSERT\s+INTO\s+" + _TABLE_TOKEN, re.IGNORECASE)),
    ("update", re.compile(r"\bUPDATE\s+" + _TABLE_TOKEN, re.IGNORECASE)),
    ("delete", re.compile(r"\bDELETE\s+FROM\s+" + _TABLE_TOKEN, re.IGNORECASE)),
)

# Identifier-shaped tokens a naive FROM/JOIN capture can grab instead of a
# table name: SQL keywords (``FROM (SELECT``) and English stopwords from prose
# that slipped past the literal gate. Normalization already rejects
# non-identifiers; this rejects identifier-shaped non-tables.
_SQL_KEYWORDS = frozenset(
    {
        "select", "where", "set", "values", "on", "using", "lateral", "unnest", "dual",
        "the", "a", "an", "this", "that", "all", "each", "your", "their", "its",
    }
)  # fmt: skip

_MIN_LITERAL_LEN = 12

# A SQL literal *opens* with SQL. Prose that happens to satisfy the verb shape
# does not: a docstring reading "The incremental update path re-parses ... would
# SELECT the whole table ... vanished from a file" clears every gate above and
# yielded the tables ``path`` and ``would``. Anchoring to the start rejects
# prose as a class instead of blocklisting the English words it leaks.
#
# The opener set is deliberately wider than the statement verbs. A query is
# routinely preceded by a named-query comment (``-- name: get_user``, the
# aiosql/yesql/sqlc/HugSQL convention), a ``CREATE TABLE x AS SELECT ... FROM
# y`` whose read of ``y`` is a real consumer, an ``EXPLAIN``, or a T-SQL
# ``DECLARE``/``SET``/``BEGIN`` preamble. Requiring a bare statement verb threw
# all of those away; the verb gate below still decides whether it is SQL.
_SQL_COMMENT_PREFIX_RE = re.compile(r"^(?:\s|--[^\n]*\n|/\*.*?\*/)+", re.DOTALL)
_SQL_OPENER_RE = re.compile(
    r"^[(\s]*(?:"
    r"WITH|SELECT|INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT"
    r"|CREATE|EXPLAIN|TRUNCATE|CALL|EXEC(?:UTE)?|DECLARE|SET|BEGIN|COPY"
    r")\b",
    re.IGNORECASE,
)


def _opens_with_sql(literal: str) -> bool:
    """True when *literal* begins with SQL, ignoring any leading comment."""
    stripped = _SQL_COMMENT_PREFIX_RE.sub("", literal, count=1)
    return bool(_SQL_OPENER_RE.match(stripped))


def _verb_for_statement(stmt: object) -> str:
    kind = type(stmt).__name__.lower()
    return kind if kind in ("insert", "update", "delete", "merge") else "select"


class SqlStringsDialect:
    """Consumers from SQL literals in app code (all supported app languages)."""

    name = "sql-strings"
    extensions = PYTHON | JS_TS | JAVA | KOTLIN | GO | CSHARP | RUBY | PHP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        seen: set[str] = set()
        for literal, line in self._sql_literals(ctx.content):
            for table_raw, verb in self._tables_in(literal):
                if table_raw.lower().strip('"`[]') in _SQL_KEYWORDS:
                    continue
                key = table_raw.lower()
                if key in seen:
                    continue
                seen.add(key)
                contract = build_table_consumer(
                    ctx, table_raw=table_raw, verb=verb, client=self.name, line=line
                )
                if contract is not None:
                    out.append(contract)
        return out

    @staticmethod
    def _sql_literals(content: str) -> list[tuple[str, int]]:
        """``(literal, line)`` for each SQL-looking string literal in *content*."""
        literals: list[tuple[str, int]] = []
        for m in _STRING_RE.finditer(content):
            s = next(g for g in m.groups() if g is not None)
            if (
                len(s) >= _MIN_LITERAL_LEN
                and _opens_with_sql(s)
                and _SQL_VERB_RE.search(s)
                and (_UPPER_VERB_RE.search(s) or _SQL_PUNCT_RE.search(s))
            ):
                literals.append((s, line_at(content, m.start())))
        return literals

    def _tables_in(self, literal: str) -> list[tuple[str, str]]:
        """``(raw_table, verb)`` pairs, AST-first with regex fallback."""
        parsed = self._parse_tables(literal)
        if parsed is not None:
            return parsed
        found: list[tuple[str, str]] = []
        for verb, pattern in _VERB_TABLE_RES:
            for m in pattern.findall(literal):
                found.append((m, verb))
        return found

    @staticmethod
    def _parse_tables(literal: str) -> list[tuple[str, str]] | None:
        """Table names via sqlglot, or ``None`` when the literal won't parse."""
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return None
        logging.getLogger("sqlglot").setLevel(logging.ERROR)
        try:
            statements = sqlglot.parse(literal, error_level=sqlglot.ErrorLevel.RAISE)
        except Exception:
            return None
        found: list[tuple[str, str]] = []
        for stmt in statements:
            if stmt is None:
                return None
            verb = _verb_for_statement(stmt)
            # A CTE alias is a name bound by this statement, not a table it
            # reads. sqlglot models a reference to one as exp.Table all the
            # same, so ``WITH ranked AS (...) ... FROM ranked`` reported a
            # table named ``ranked``. Only unqualified references shadow.
            cte_names = {
                cte.alias.lower() for cte in stmt.find_all(exp.CTE) if cte.alias
            }
            for table in stmt.find_all(exp.Table):
                name = table.name
                if not name:
                    continue
                if not table.db and name.lower() in cte_names:
                    continue
                raw = f"{table.db}.{name}" if table.db else name
                found.append((raw, verb))
        return found if found else None
