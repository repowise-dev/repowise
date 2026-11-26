"""Which function issues a database statement: the statement-site join.

A production source reports statements by their normalized text
(``pg_stat_statements`` style, values as ``$1``). This module reads that text
into a :class:`StatementShape` (what it does: the verb, the table or
database function, its columns and filter columns, and whether PostgREST
built it) and joins it to the :class:`StatementSite` rows the static index
stores (:mod:`repowise.core.workspace.statement_sites`): the places in the
code that issue a statement, and the database schedules that do.

:func:`match_statement` is the one join, pure, for ``evidence add`` and for
an evidence source's batches alike. Never a wrong function before recall:

* ``function``: every site that could issue the statement sits in one
  function, so the row names it;
* ``ambiguous``: sites in more than one function could (or one the index
  cannot name), so it names none;
* ``database_schedule``: only a database schedule (``cron.schedule``) runs
  it, no code does;
* ``None``: no site matches; the row stays unjoined.

A site is a candidate when nothing it says contradicts the statement: the
same channel (PostgREST builds ``WITH pgrst_source AS (...)``; literal SQL,
an ORM and a schedule send their own text), the same table or function, the
same verb, and columns and filter columns that agree as far as each side
knows them. A site whose table or function the index could not read (a
name only known at run time) is a candidate for every statement of its verb
and channel, so it can only make a join ambiguous, never wrong.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

#: How a statement reached the database.
CHANNELS = ("postgrest", "sql")
VERBS = ("select", "insert", "update", "delete", "call")
#: What a join says about a statement (``None``: unjoined).
ISSUERS = ("function", "ambiguous", "database_schedule")

_NAME = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
_QUALIFIED_RE = re.compile(rf"({_NAME})(?:\s*\.\s*({_NAME}))?(?:\s*\.\s*({_NAME}))?")
_PGRST_RE = re.compile(r"^\s*WITH\s+pgrst_source\s+AS\s*\(", re.IGNORECASE)
_LEAD_RE = re.compile(r"^\s*\(*\s*(select|insert|update|delete|with)\b", re.IGNORECASE)
_INSERT_RE = re.compile(rf"^\s*INSERT\s+INTO\s+({_NAME}(?:\s*\.\s*{_NAME})?)\s*(\()?", re.I)
_UPDATE_RE = re.compile(rf"^\s*UPDATE\s+(?:ONLY\s+)?({_NAME}(?:\s*\.\s*{_NAME})?)\s+", re.I)
_DELETE_RE = re.compile(rf"^\s*DELETE\s+FROM\s+(?:ONLY\s+)?({_NAME}(?:\s*\.\s*{_NAME})?)", re.I)
_FROM_RE = re.compile(rf"\bFROM\s+(?:ONLY\s+)?(?:({_NAME}(?:\s*\.\s*{_NAME})?)\s*(\()?|\()", re.I)
_SELECT_CALL_RE = re.compile(rf"^\s*SELECT\s+({_NAME}(?:\s*\.\s*{_NAME})?)\s*\(", re.I)
# PostgREST calls a function in ``LATERAL "s"."f"(...)``, ``SELECT "s"."f"(...)``
# or ``FROM "s"."f"() pgrst_call``.
_PGRST_CALL_RE = re.compile(rf"(?:LATERAL|SELECT|FROM)\s+({_NAME}\s*\.\s*{_NAME})\s*\(", re.I)
_RECORD_RE = re.compile(r"json_to_rec(?:ord|ordset)\s*\([^)]*\)\s*AS\s+_\s*\(", re.I)
_NAMED_ARG_RE = re.compile(rf"({_NAME})\s*:=")
_WHERE_RE = re.compile(r"\bWHERE\b", re.I)
_WHERE_END_RE = re.compile(
    r"\b(?:ORDER\s+BY|GROUP\s+BY|LIMIT|OFFSET|RETURNING|HAVING|FOR\s+UPDATE|UNION)\b", re.I
)
_CONDITION_RE = re.compile(
    rf"({_NAME}(?:\s*\.\s*{_NAME}){{0,2}})\s*"
    r"(?:->>?|#>>?|=|<>|!=|<=|>=|<<|>>|-\|-|&<|&>|<|>|~~\*?|!~~\*?|\bI?LIKE\b|\bIS\b"
    r"|\bIN\b|\bBETWEEN\b|\bNOT\b|@>|<@|&&|@@)",
    re.I,
)
_SET_RE = re.compile(r"\bSET\b(.*?)(?:\bWHERE\b|\bRETURNING\b|\bFROM\b|$)", re.I | re.S)
_SET_COLUMN_RE = re.compile(rf"(?:^|,)\s*({_NAME})\s*=")
_SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema"})
_KEYWORDS = frozenset(
    {"and", "or", "not", "select", "case", "when", "then", "else", "end", "null", "true", "false"}
)


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        return name[1:-1].replace('""', '"').lower()
    return name.lower()


def _split_qualified(text: str) -> tuple[str | None, str]:
    match = _QUALIFIED_RE.match(text.strip())
    if match is None:
        return None, _unquote(text)
    parts = [_unquote(p) for p in match.groups() if p]
    if len(parts) == 1:
        return None, parts[0]
    return parts[-2], parts[-1]


def _balanced(text: str, open_at: int) -> str | None:
    """The text inside the parenthesis at *open_at*; None when the text ends first."""
    depth = 0
    for i in range(open_at, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : i]
    return None


def _names_in(text: str | None) -> frozenset[str] | None:
    if text is None:
        return None  # cut short: the list may go on
    out = set()
    for part in _split_top(text):
        part = part.strip()
        if not part:
            continue
        first = re.match(_NAME, part)
        if first is not None:
            out.add(_unquote(first.group(0)))
    return frozenset(out)


def _split_top(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


@dataclass(frozen=True)
class Columns:
    """Column (or argument) names as one side knows them.

    ``required`` always appear, ``optional`` may, and ``closed`` says nothing
    else can. A statement's own columns are closed with nothing optional.
    """

    required: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    closed: bool = True

    def agrees(self, statement: frozenset[str]) -> bool:
        """Whether a statement with exactly *statement* could come from here.

        Names compare in lower case: a statement's text folds unquoted names,
        and code spells a quoted one (``userId``) in its own case.
        """
        statement = frozenset(c.lower() for c in statement)
        required = frozenset(c.lower() for c in self.required)
        if not required <= statement:
            return False
        if not self.closed:
            return True
        return statement <= required | frozenset(c.lower() for c in self.optional)

    def as_json(self) -> dict[str, object]:
        return {
            "required": sorted(self.required),
            "optional": sorted(self.optional),
            "closed": self.closed,
        }

    @classmethod
    def from_json(cls, raw: object) -> Columns | None:
        if not isinstance(raw, dict):
            return None
        return cls(
            frozenset(str(c) for c in raw.get("required") or ()),
            frozenset(str(c) for c in raw.get("optional") or ()),
            bool(raw.get("closed", True)),
        )


@dataclass(frozen=True)
class StatementShape:
    """What one normalized statement does, as far as its text says.

    ``columns`` is what it writes (insert, update), reads (select) or passes
    (a call's named arguments); ``filters`` the columns its ``WHERE`` tests.
    None means the text does not say (cut short, or ``SELECT *``).
    """

    channel: str
    verb: str
    name: str
    schema: str | None = None
    columns: frozenset[str] | None = None
    filters: frozenset[str] | None = None


def statement_shape(text: str | None) -> StatementShape | None:
    """The shape of a normalized statement, or None when it is none this reads.

    Reads ``pg_stat_statements`` text (values as ``$n``) and literal SQL alike:
    the statement's first verb and its first table, or the database function
    a bare ``SELECT f(...)`` or a PostgREST call runs. A ``WITH`` that is not
    PostgREST's, a ``SET`` or anything else is None.
    """
    if not text:
        return None
    text = " ".join(text.split())
    pgrst = _PGRST_RE.match(text)
    if pgrst is not None:
        inner = _balanced(text, pgrst.end() - 1)
        if inner is None:
            return _postgrest_shape(text[pgrst.end() :], complete=False)
        return _postgrest_shape(inner, complete=True)
    lead = _LEAD_RE.match(text)
    if lead is None or lead.group(1).lower() == "with":
        return None
    return _sql_shape(text.lstrip("( "), "sql", complete=text.count("(") == text.count(")"))


def _postgrest_shape(inner: str, *, complete: bool) -> StatementShape | None:
    if "pgrst_call" in inner:
        call = next(
            (
                m
                for m in _PGRST_CALL_RE.finditer(inner)
                if _split_qualified(m.group(1))[0] not in _SYSTEM_SCHEMAS
            ),
            None,
        )
        if call is None:
            return None  # cut short before the function's name
        schema, name = _split_qualified(call.group(1))
        return StatementShape("postgrest", "call", name, schema, _call_arguments(inner))
    return _sql_shape(inner.strip(), "postgrest", complete=complete)


def _call_arguments(inner: str) -> frozenset[str] | None:
    record = _RECORD_RE.search(inner)
    if record is not None:
        return _names_in(_balanced(inner, record.end() - 1))
    named = _NAMED_ARG_RE.findall(inner)
    if named:
        return frozenset(_unquote(n) for n in named)
    # ``"s"."f"()`` with no payload: a call with no arguments.
    call = _PGRST_CALL_RE.search(inner)
    if call is not None and inner[call.end() :].lstrip().startswith(")"):
        return frozenset()
    return None


def _sql_shape(text: str, channel: str, *, complete: bool) -> StatementShape | None:
    """The shape of one statement; a text cut short (*complete* False) says
    nothing about its filters, or about a column list it does not close."""
    filters = _filters(text) if complete else None
    insert = _INSERT_RE.match(text)
    if insert is not None:
        schema, name = _split_qualified(insert.group(1))
        columns = _names_in(_balanced(text, insert.end() - 1)) if insert.group(2) else None
        return StatementShape(channel, "insert", name, schema, columns)
    update = _UPDATE_RE.match(text)
    if update is not None:
        schema, name = _split_qualified(update.group(1))
        columns = _set_columns(text, update.end() - 1) if complete else None
        return StatementShape(channel, "update", name, schema, columns, filters)
    delete = _DELETE_RE.match(text)
    if delete is not None:
        schema, name = _split_qualified(delete.group(1))
        return StatementShape(channel, "delete", name, schema, None, filters)
    if not re.match(r"\s*SELECT\b", text, re.I):
        return None
    return _select_shape(text, channel, filters)


def _set_columns(text: str, at: int) -> frozenset[str] | None:
    set_clause = _SET_RE.search(text, at)
    if set_clause is None:
        return None
    return frozenset(_unquote(c) for c in _SET_COLUMN_RE.findall(set_clause.group(1))) or None


def _select_shape(text: str, channel: str, filters: frozenset[str] | None) -> StatementShape | None:
    """A ``SELECT``: the table its own ``FROM`` names, or the function it calls."""
    source = next((m for m in _FROM_RE.finditer(text) if _depth(text, m.start()) == 0), None)
    if source is None:
        call = _SELECT_CALL_RE.match(text)
        schema, name = _split_qualified(call.group(1)) if call else (None, "")
        if call is None or schema in _SYSTEM_SCHEMAS or name in _KEYWORDS:
            return None
        return StatementShape(channel, "call", name, schema)
    if source.group(1) is None:
        return None  # ``FROM (subquery)``: no table of its own
    schema, name = _split_qualified(source.group(1))
    if source.group(2):
        # ``FROM f(...)``: the statement reads what a function returns.
        return StatementShape(channel, "call", name, schema)
    return StatementShape(channel, "select", name, schema, _selected(text, source.start()), filters)


def _depth(text: str, at: int) -> int:
    """How many parentheses are open at *at*."""
    return text.count("(", 0, at) - text.count(")", 0, at)


def _selected(text: str, from_at: int) -> frozenset[str] | None:
    head = re.match(r"\s*SELECT\s+(?:DISTINCT\s+)?", text, re.I)
    if head is None:
        return None
    out = set()
    for item in _split_top(text[head.end() : from_at]):
        item = item.strip()
        if not item or "*" in item or "(" in item:
            return None
        parts = [_unquote(p) for p in re.findall(_NAME, item.split(" AS ")[0])]
        if not parts:
            return None
        out.add(parts[-1])
    return frozenset(out) or None


def _filters(text: str) -> frozenset[str] | None:
    where = _WHERE_RE.search(text)
    if where is None:
        return frozenset()
    end = _WHERE_END_RE.search(text, where.end())
    clause = text[where.end() : end.start() if end else len(text)]
    out = set()
    for match in _CONDITION_RE.finditer(clause):
        name = _split_qualified(match.group(1))[1]
        if name not in _KEYWORDS and not name.startswith("$"):
            out.add(name)
    return frozenset(out)


@dataclass(frozen=True)
class StatementSite:
    """One place that issues a statement: a code site or a database schedule.

    ``name`` is the table or database function, lower case; None when the
    index could not read it (it is only known at run time). ``verb`` None:
    the site's call chain does not say. ``symbol_id`` is the function holding
    the site (None at module level, or for a schedule). ``via`` says how the
    name was read: ``literal``, ``constant``, ``wrapper`` (a helper's caller
    passed it), ``data`` (a loop over a constant table), ``model`` (an ORM
    model), ``sql`` (literal SQL) or ``schedule``.
    """

    file_path: str
    line: int
    channel: str
    target: str
    name: str | None
    verb: str | None
    via: str
    symbol_id: str | None = None
    schema: str | None = None
    columns: Columns | None = None
    filters: Columns | None = None

    @property
    def scheduled(self) -> bool:
        return self.via == "schedule"


@dataclass(frozen=True)
class StatementMatch:
    """What the join found for one statement (see the module docstring)."""

    issuer: str | None
    symbol_id: str | None = None
    #: Every matching site, for a caller that wants to show them.
    sites: tuple[StatementSite, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str | None:
        """Why no function was named, as a skip or unlinked reason."""
        if self.issuer == "function":
            return None
        if self.issuer is None:
            return "no_statement_site"
        return "ambiguous_statement" if self.issuer == "ambiguous" else self.issuer


def _target(verb: str) -> str:
    return "rpc" if verb == "call" else "table"


def _agrees(site: StatementSite, shape: StatementShape) -> bool:
    channel = "sql" if site.scheduled else site.channel
    if channel != shape.channel or site.target != _target(shape.verb):
        return False
    if site.verb is not None and site.verb != shape.verb:
        return False
    if site.name is not None and not _same_relation(site, shape):
        return False
    return _fits(site.columns, shape.columns) and _fits(site.filters, shape.filters)


def _same_relation(site: StatementSite, shape: StatementShape) -> bool:
    """The same table or function, in the same schema as far as both say."""
    if site.name != shape.name:
        return False
    if site.schema == "?":
        return True  # a schema only known at run time
    if site.schema is None:
        return shape.schema in (None, "public")
    return shape.schema is None or site.schema == shape.schema


def _fits(known: Columns | None, statement: frozenset[str] | None) -> bool:
    return known is None or statement is None or known.agrees(statement)


def match_statement(shape: StatementShape | None, sites: Sequence[StatementSite]) -> StatementMatch:
    """Join one statement to the sites that may issue it (never a guess)."""
    if shape is None:
        return StatementMatch(None)
    found = tuple(s for s in sites if _agrees(s, shape))
    code = [s for s in found if not s.scheduled]
    if not code:
        # Only a schedule whose job names this table or function says it runs it.
        named = any(s.name is not None for s in found)
        return StatementMatch("database_schedule" if named else None, sites=found)
    named = {s.symbol_id for s in code if s.name is not None}
    # A site with no function, or one whose table the index could not read,
    # leaves the issuer open.
    if (
        len(named) == 1
        and None not in named
        and all(s.name is not None for s in code)
        and not any(s.scheduled for s in found)
    ):
        return StatementMatch("function", next(iter(named)), found)
    # Several functions, or one whose table is only known at run time (alone
    # too): it may be the issuer, so none is named.
    return StatementMatch("ambiguous", sites=found)


def match_statements(
    shapes: Iterable[StatementShape | None], sites: Sequence[StatementSite]
) -> list[StatementMatch]:
    """:func:`match_statement` for each of *shapes*, in order."""
    return [match_statement(shape, sites) for shape in shapes]


__all__ = [
    "CHANNELS",
    "ISSUERS",
    "VERBS",
    "Columns",
    "StatementMatch",
    "StatementShape",
    "StatementSite",
    "match_statement",
    "match_statements",
    "statement_shape",
]
