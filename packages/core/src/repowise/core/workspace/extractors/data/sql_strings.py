"""Consumer dialect: SQL string literals embedded in application code.

Precision rules (this is where the false links would come from):

1. Only *string literals* are considered, and only ones that contain a SQL
   verb; a prose comment mentioning "from users" never matches because it is
   not inside a string carrying a verb shape.
2. When the literal parses under sqlglot, table names come from the AST (the
   high-precision path). When parsing fails (interpolation placeholders break
   the grammar), a verb-anchored regex extracts the table token instead; a name the
   literal binds with ``WITH`` is not a table on either path. The target of an
   ``INSERT``, ``UPDATE`` or ``DELETE`` is a table on the regex path, as
   PostgreSQL reads it, though a ``WITH`` binds its name; the parsed path reads
   such a target as the CTE, as SQL Server does.
3. A table token that is itself interpolated (``FROM {table}`` /
   ``FROM ${table}`` / ``FROM " + name``) is skipped: normalization rejects
   non-identifier tokens, so an interpolated target emits nothing.
4. In Java and Kotlin, JPQL queries (ORM entity-class queries in
   ``@Query(...)`` without ``nativeQuery = true``, in ``@NamedQuery`` /
   ``@NamedQueries``, and the first argument of ``createQuery(``) are skipped
   so that entity names are not extracted as tables. Native queries (e.g.
   ``@Query(..., nativeQuery = true)`` and ``createNativeQuery(``) continue
   to emit their table targets.
"""

from __future__ import annotations

import bisect
import functools
import re
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from repowise.core.workspace.origins import ContractOrigin

from ..base import ScanContext, line_at
from ..langs import CSHARP, GO, JAVA, JS_TS, KOTLIN, PHP, PYTHON, RUBY
from ..strings import match_paren, split_top_level
from .dialect import build_table_consumer

if TYPE_CHECKING:
    from sqlglot import Tokenizer

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
    r'|(?:\$@|@\$|@)"(?P<verb>(?:[^"]|"")*)"'
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
_WRITE_VERBS = frozenset({"insert", "update", "delete"})

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


# ``WITH name [(columns)] AS (``: one named subquery of a WITH clause. The next one follows a
# comma. A name may be quoted the way a dialect quotes an identifier: ``"name"``, ```name```,
# ``[name]``.
_CTE_HEAD_RE = re.compile(
    r'\s*(?P<name>"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_]\w*)\s*(?:\([^()]*\))?\s*AS\s*'
    r"(?:NOT\s+MATERIALIZED\s*|MATERIALIZED\s*)?\(",
    re.IGNORECASE,
)
_WITH_RE = re.compile(r"\bWITH\s+(?:RECURSIVE\s+)?", re.IGNORECASE)


def _unquote(name: str) -> str:
    """The name of one quoted identifier without its quotes (``"a"``, ```a```, ``[a]``).

    Any other text is returned as it is.
    """
    if len(name) > 1 and (name[0], name[-1]) in (('"', '"'), ("`", "`"), ("[", "]")):
        return name[1:-1]
    return name


# A literal sqlglot's tokenizer cannot read: the quoted names and strings are taken whole, and the
# comments are what the scan meets outside them, from left to right. A block comment opens
# another inside it, as the tokenizer reads it, so ``_block_comment_end`` finds its end.
_UNREAD_SCAN_RE = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|\[[^\]]*\]|(?P<comment>--[^\n]*|/\*)",
    re.DOTALL,
)
_BLOCK_COMMENT_MARK_RE = re.compile(r"/\*|\*/")
_NOT_SPACE_RE = re.compile(r"\S")


@functools.lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer | None:
    """sqlglot's tokenizer with the three ways a name is quoted: ``"a"``, ```a``` and ``[a]``. None
    where sqlglot is not installed."""
    try:
        from sqlglot import Tokenizer
    except ImportError:
        return None

    class Quoted(Tokenizer):
        IDENTIFIERS: ClassVar[list[str | tuple[str, str]]] = ['"', "`", ("[", "]")]

    return Quoted()


def _block_comment_end(literal: str, start: int) -> int:
    """The offset after the block comment that opens at *start*, or the end of *literal*.

    A ``/*`` inside the comment opens one that its own ``*/`` closes, as sqlglot's tokenizer
    reads it.
    """
    depth = 0
    for mark in _BLOCK_COMMENT_MARK_RE.finditer(literal, start):
        depth += 1 if mark.group(0) == "/*" else -1
        if depth == 0:
            return mark.end()
    return len(literal)


def _blank_comments_unread(literal: str) -> str:
    """*literal* with its comments blanked, for a literal the tokenizer cannot read."""
    out: list[str] = []
    pos = 0
    while (m := _UNREAD_SCAN_RE.search(literal, pos)) is not None:
        if m.group("comment") is None:
            out.append(literal[pos : m.end()])
            pos = m.end()
            continue
        end = _block_comment_end(literal, m.start()) if m.group(0) == "/*" else m.end()
        out.append(literal[pos : m.start()])
        out.append(_NOT_SPACE_RE.sub(" ", literal[m.start() : end]))
        pos = end
    out.append(literal[pos:])
    return "".join(out)


def _without_comments(literal: str) -> str:
    """*literal* with its ``--`` and ``/* */`` comments blanked, offsets and line breaks kept.

    A comment between two named subqueries then does not end the WITH clause. The comments are
    those sqlglot's tokenizer skips: the text between two tokens that is not white space. A
    literal the tokenizer cannot read has its comments read by a left to right scan that takes
    each quoted name or string whole.
    """
    tokenizer = _tokenizer()
    if tokenizer is None:
        return literal
    try:
        from sqlglot.errors import TokenError

        tokens = tokenizer.tokenize(literal)
    except TokenError:
        return _blank_comments_unread(literal)
    out: list[str] = []
    end = -1  # the offset of the last character of the previous token
    for token in tokens:
        out.append(_NOT_SPACE_RE.sub(" ", literal[end + 1 : token.start]))
        out.append(literal[token.start : token.end + 1])
        end = token.end
    out.append(_NOT_SPACE_RE.sub(" ", literal[end + 1 :]))
    return "".join(out)


class _CteScope(NamedTuple):
    """One WITH clause of a literal: where it serves and the named subqueries it holds."""

    start: int  # the offset of WITH
    end: int  # the offset where the query that WITH serves ends
    recursive: bool
    names: list[str]  # lower-cased and unquoted, in the order written
    bodies: list[tuple[int, int]]  # the offsets of the ( and ) of each body


_OPEN_PAREN_RE = re.compile(r"\(")


def _scope_end(literal: str, at: int, statement_ends: list[int]) -> int:
    """The offset where the query written at *at* ends: its statement or its parenthesis."""
    end = statement_ends[bisect.bisect_left(statement_ends, at)]
    for opener in _OPEN_PAREN_RE.finditer(literal, 0, at):
        close = match_paren(literal, opener.start(), len(literal))
        if at < close < end:
            end = close
    return end


def _cte_scopes(literal: str) -> list[_CteScope]:
    """The WITH clauses of *literal*, each with its names, their bodies and where it serves.

    A name is bound with ``WITH name [(columns)] AS (``; the next one follows a comma. Comments
    are read out first. A name is no table, though ``FROM name`` reads it. Limit: a ``(`` inside a
    string literal before a WITH can move the end of its scope outward, which only makes the scope
    too large.
    """
    literal = _without_comments(literal)
    statement_ends: list[int] = []
    offset = 0
    for statement in split_top_level(literal, ";"):
        offset += len(statement)
        statement_ends.append(offset)
        offset += 1
    scopes: list[_CteScope] = []
    for start in _WITH_RE.finditer(literal):
        at = start.end()
        names: list[str] = []
        bodies: list[tuple[int, int]] = []
        while head := _CTE_HEAD_RE.match(literal, at):
            names.append(_unquote(head.group("name")).lower())
            close = match_paren(literal, head.end() - 1, len(literal))
            bodies.append((head.end() - 1, close if close >= 0 else len(literal)))
            after = literal[close + 1 :].lstrip() if close >= 0 else ""
            if close < 0 or not after.startswith(","):
                break
            at = literal.index(",", close) + 1
        if names:
            end = _scope_end(literal, start.start(), statement_ends)
            recursive = "recursive" in start.group(0).lower()
            scopes.append(_CteScope(start.start(), end, recursive, names, bodies))
    return scopes


def _names_bound_at(scopes: list[_CteScope], at: int) -> set[str]:
    """The names that a table reference written at offset *at* sees.

    For each WITH around the reference: every name of it where the reference is in the query it
    serves; the names before the CTE whose body holds the reference (all of them under WITH
    RECURSIVE) where it is in a body.
    """
    bound: set[str] = set()
    for scope in scopes:
        if not scope.start <= at < scope.end:
            continue
        inside = next((i for i, (a, b) in enumerate(scope.bodies) if a < at < b), None)
        if inside is None or scope.recursive:
            bound.update(scope.names)
        else:
            bound.update(scope.names[:inside])
    return bound


def _bound_names(table: object, exp: object) -> set[str]:
    """The lower-cased CTE names that a reference *table* sees.

    For each WITH around the reference: every name of it where the reference is in the query it
    serves; the names before the CTE whose body holds the reference (and that CTE's own for
    WITH RECURSIVE) where it is in a body. *exp* is ``sqlglot.exp``.
    """
    bound: set[str] = set()
    node = table
    while (parent := node.parent) is not None:
        if isinstance(parent, exp.CTE) and isinstance(parent.parent, exp.With):
            with_ = parent.parent
            names = [c.alias.lower() for c in with_.expressions]
            at = with_.expressions.index(parent)
            bound.update(names if with_.args.get("recursive") else names[:at])
        elif (with_ := parent.args.get("with")) is not None and node is not with_:
            bound.update(c.alias.lower() for c in with_.expressions)
        node = parent
    return bound


def _opens_with_sql(literal: str) -> bool:
    """True when *literal* begins with SQL, ignoring any leading comment."""
    stripped = _SQL_COMMENT_PREFIX_RE.sub("", literal, count=1)
    return bool(_SQL_OPENER_RE.match(stripped))


def _verb_for_statement(stmt: object) -> str:
    kind = type(stmt).__name__.lower()
    return kind if kind in ("insert", "update", "delete", "merge") else "select"


_JVM_EXTENSIONS = JAVA | KOTLIN

_JPQL_QUERY_RE = re.compile(r"(?<![A-Za-z0-9_])(?:@|\.)?Query\b")
_JPQL_NAMED_QUERY_RE = re.compile(r"@(NamedNativeQuery|NamedQuery)\b")
_JPQL_NATIVE_FLAG_RE = re.compile(r"\bnativeQuery\s*=\s*true\b")
_CREATE_QUERY_RE = re.compile(r"(?<![A-Za-z0-9_])createQuery\s*\($")


def _is_enclosing_open(content: str, opener_pos: int, target_pos: int) -> bool:
    """True when *target_pos* lies inside the parentheses opened at *opener_pos*."""
    paren_idx = content.find("(", opener_pos, target_pos)
    if paren_idx == -1:
        return False
    depth = 0
    in_str = False
    quote = ""
    i = paren_idx
    while i < target_pos:
        ch = content[i]
        if in_str:
            if ch == "\\" and i + 1 < target_pos:
                i += 2
                continue
            if content.startswith(quote, i):
                in_str = False
                i += len(quote)
                continue
        else:
            if content.startswith(('"""', "'''"), i):
                in_str = True
                quote = content[i : i + 3]
                i += 3
                continue
            if ch in ('"', "'", "`"):
                in_str = True
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return False
        i += 1
    return depth > 0


def _find_enclosing_block(content: str, opener_pos: int) -> str | None:
    """The span from *opener_pos* to matching ``)``, or None if unparenthesized."""
    paren_idx = content.find("(", opener_pos)
    if paren_idx == -1:
        return None
    depth = 0
    in_str = False
    quote = ""
    i = paren_idx
    n = len(content)
    while i < n:
        ch = content[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if content.startswith(quote, i):
                in_str = False
                i += len(quote)
                continue
        else:
            if content.startswith(('"""', "'''"), i):
                in_str = True
                quote = content[i : i + 3]
                i += 3
                continue
            if ch in ('"', "'", "`"):
                in_str = True
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return content[opener_pos : i + 1]
        i += 1
    return content[opener_pos:]


def _is_jpql(
    content: str,
    start: int,
    named_positions: list[int] | None = None,
    query_positions: list[int] | None = None,
) -> bool:
    """True when string literal at *start* is a Java/Kotlin JPQL query."""
    window = content[max(0, start - 200) : start]
    prefix = re.sub(r"(?:\s|//[^\n]*\n|/\*.*?\*/)+$", "", window)
    if _CREATE_QUERY_RE.search(prefix):
        return True

    if named_positions is None:
        named_positions = [
            m.start()
            for m in _JPQL_NAMED_QUERY_RE.finditer(content[:start])
            if m.group(1) == "NamedQuery"
        ]
    idx = bisect.bisect_right(named_positions, start) - 1
    if idx >= 0:
        pos = named_positions[idx]
        if _is_enclosing_open(content, pos, start):
            return True

    if query_positions is None:
        query_positions = [m.start() for m in _JPQL_QUERY_RE.finditer(content[:start])]
    idx = bisect.bisect_right(query_positions, start) - 1
    if idx >= 0:
        pos = query_positions[idx]
        if _is_enclosing_open(content, pos, start):
            block = _find_enclosing_block(content, pos)
            if block is not None and not _JPQL_NATIVE_FLAG_RE.search(block):
                return True

    return False


class SqlStringsDialect:
    """Consumers from SQL literals in app code (all supported app languages)."""

    name = "sql-strings"
    extensions = PYTHON | JS_TS | JAVA | KOTLIN | GO | CSHARP | RUBY | PHP
    # A SQL literal holds one of the verbs `_SQL_VERB_RE` reads, in some case.
    requires = tuple(
        spelling
        for verb in ("select", "insert", "update", "delete", "merge")
        for spelling in (verb.upper(), verb, verb.capitalize())
    )

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        seen: set[str] = set()
        for literal, line in self._sql_literals(ctx.content, ctx.suffix):
            for table_raw, verb in self._tables_in(literal):
                if table_raw.lower().strip('"`[]') in _SQL_KEYWORDS:
                    continue
                key = table_raw.lower()
                if key in seen:
                    continue
                seen.add(key)
                contract = build_table_consumer(
                    ctx,
                    table_raw=table_raw,
                    verb=verb,
                    client=self.name,
                    line=line,
                    origin=ContractOrigin.SQL_STRING,
                )
                if contract is not None:
                    out.append(contract)
        return out

    @classmethod
    def _sql_literals(cls, content: str, suffix: str = "") -> list[tuple[str, int]]:
        """``(literal, line)`` for each SQL-looking string literal in *content*."""
        literals: list[tuple[str, int]] = []
        is_jvm = suffix in _JVM_EXTENSIONS
        named_positions: list[int] | None = None
        query_positions: list[int] | None = None
        if is_jvm:
            named_positions = [
                m.start()
                for m in _JPQL_NAMED_QUERY_RE.finditer(content)
                if m.group(1) == "NamedQuery"
            ]
            query_positions = [m.start() for m in _JPQL_QUERY_RE.finditer(content)]
        for m in _STRING_RE.finditer(content):
            s = next(g for g in m.groups() if g is not None)
            if (
                len(s) >= _MIN_LITERAL_LEN
                and _opens_with_sql(s)
                and _SQL_VERB_RE.search(s)
                and (_UPPER_VERB_RE.search(s) or _SQL_PUNCT_RE.search(s))
            ):
                if is_jvm and _is_jpql(content, m.start(), named_positions, query_positions):
                    continue
                literals.append((s, line_at(content, m.start())))
        return literals

    def _tables_in(self, literal: str) -> list[tuple[str, str]]:
        """``(raw_table, verb)`` pairs, AST-first with regex fallback."""
        parsed = self._parse_tables(literal)
        if parsed is not None:
            return parsed
        found: list[tuple[str, str]] = []
        scopes = _cte_scopes(literal)
        for verb, pattern in _VERB_TABLE_RES:
            for m in pattern.finditer(literal):
                name = m.group(1)
                # The target of a write is a table, though a WITH binds its name.
                if verb in _WRITE_VERBS or (
                    _unquote(name).lower() not in _names_bound_at(scopes, m.start(1))
                ):
                    found.append((name, verb))
        return found

    @staticmethod
    def _parse_tables(literal: str) -> list[tuple[str, str]] | None:
        """Table names via sqlglot, or ``None`` when the literal won't parse."""
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return None
        from repowise.core.ingestion.sql_parse import parse_sql

        try:
            statements = parse_sql(
                literal, error_level=sqlglot.ErrorLevel.RAISE, psql_meta_commands=False
            )
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
            # table named ``ranked``. Only unqualified references shadow, and
            # only those that come after the binding: inside the body of a
            # CTE, the names of the CTEs before it are bound, and its own (and
            # the later ones) only under WITH RECURSIVE.
            for table in stmt.find_all(exp.Table):
                name = table.name
                if not name:
                    continue
                if not table.db and name.lower() in _bound_names(table, exp):
                    continue
                raw = f"{table.db}.{name}" if table.db else name
                found.append((raw, verb))
        return found if found else None
