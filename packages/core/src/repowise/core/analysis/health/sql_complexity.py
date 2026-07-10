"""SQL health walker: routine complexity + high-precision statement smells.

The SQL analogue of ``complexity.walker.walk_file``, registered as a dialect
of the health engine's walk step (``engine._walk`` routes ``language ==
"sql"`` here; every other language keeps the tree-sitter path). Output rides
the same ``FileComplexity`` schema, and the smells travel as ``PerfHit``
records with ``sql_*`` kinds so the existing biomarker pipeline lifts them
into findings.

Two analysis tiers, matching what sqlglot can actually see:

* **Statements** (``CREATE VIEW``, top-level DML) parse into a full AST;
  the three smells (``sql_select_star``, ``sql_update_delete_without_where``,
  ``sql_cartesian_join``) are detected on AST shape, precision-first.
* **Routine bodies** (T-SQL procedures, PL/pgSQL ``$$`` bodies) do NOT
  parse: sqlglot degrades them to ``Command``/``Heredoc`` text. Cyclomatic
  complexity is therefore counted from the body text (comment- and
  string-stripped keyword counting), and nesting is not reported: a text
  scan cannot measure it reliably across dialects, so we don't guess.

Every SQL marker is maintainability/performance-only (see
``scoring._BIOMARKER_DIMENSIONS``): none of this is defect-calibrated, so
none of it may move the surfaced defect score.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .complexity import FileComplexity, FunctionComplexity, PerfHit
from .complexity.nloc import _count_file_nloc

# CCN threshold for emitting a ``sql_high_complexity`` hit. Matches the
# complex_method sensibility: a routine below 10 decision points is routine.
_CCN_THRESHOLD = 10

# ---------------------------------------------------------------------------
# Routine discovery + text-based CCN
# ---------------------------------------------------------------------------

_ROUTINE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?(?:DEFINER\s*=\s*\S+\s+)?"
    r"(?:FUNCTION|PROCEDURE|PROC)\s+([\w.\"\[\]`$]+)",
    re.IGNORECASE,
)

_COMMENT_LINE_RE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r"'[^']*'")

_IF_RE = re.compile(r"\bIF\b", re.IGNORECASE)
_END_IF_RE = re.compile(r"\bEND\s+IF\b", re.IGNORECASE)
_ELSIF_RE = re.compile(r"\bELS(?:E)?IF\b", re.IGNORECASE)
_WHEN_RE = re.compile(r"\bWHEN\b", re.IGNORECASE)
_WHILE_RE = re.compile(r"\bWHILE\b", re.IGNORECASE)
_LOOP_RE = re.compile(r"\bLOOP\b", re.IGNORECASE)
_END_LOOP_RE = re.compile(r"\bEND\s+LOOP\b", re.IGNORECASE)
_BOOL_RE = re.compile(r"\b(?:AND|OR)\b", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"\bBETWEEN\b", re.IGNORECASE)


def _strip_noise(body: str) -> str:
    body = _COMMENT_BLOCK_RE.sub(" ", body)
    body = _COMMENT_LINE_RE.sub(" ", body)
    return _STRING_RE.sub("''", body)


def _body_ccn(body: str) -> int:
    """Cyclomatic complexity of a routine body via decision-keyword counting.

    ``END IF`` is subtracted from the ``IF`` count (same token); loop openers
    are the net ``LOOP`` count where the dialect uses ``LOOP``/``END LOOP``
    (PL/pgSQL: ``WHILE c LOOP`` nets to one), else the ``WHILE`` count
    (T-SQL). ``BETWEEN``'s mandatory ``AND`` is not a decision, so one AND per
    BETWEEN is subtracted.
    """
    text = _strip_noise(body)
    ifs = len(_IF_RE.findall(text)) - len(_END_IF_RE.findall(text))
    elsifs = len(_ELSIF_RE.findall(text))
    whens = len(_WHEN_RE.findall(text))
    net_loops = len(_LOOP_RE.findall(text)) - len(_END_LOOP_RE.findall(text))
    loops = net_loops if net_loops > 0 else len(_WHILE_RE.findall(text))
    bools = len(_BOOL_RE.findall(text)) - len(_BETWEEN_RE.findall(text))
    return 1 + max(ifs, 0) + elsifs + whens + max(loops, 0) + max(bools, 0)


def _bare_name(raw: str) -> str:
    last = raw.split(".")[-1]
    return last.strip('"[]`')


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _walk_routines(text: str) -> tuple[list[FunctionComplexity], list[PerfHit]]:
    """Per-routine CCN from the raw file text.

    A routine's body runs to the next ``CREATE FUNCTION/PROCEDURE`` header or
    EOF: good enough for keyword counting even when trailing statements
    (``LANGUAGE plpgsql``) ride along, since they carry no decision keywords.
    """
    matches = list(_ROUTINE_RE.finditer(text))
    functions: list[FunctionComplexity] = []
    hits: list[PerfHit] = []
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : body_end]
        name = _bare_name(m.group(1))
        if not name:
            continue
        start_line = _line_of(text, m.start())
        end_line = _line_of(text, body_end - 1)
        ccn = _body_ccn(body)
        nloc = sum(1 for ln in body.splitlines() if ln.strip())
        functions.append(
            FunctionComplexity(
                name=name,
                start_line=start_line,
                end_line=end_line,
                ccn=ccn,
                max_nesting=0,
                cognitive=0,
                nloc=nloc,
            )
        )
        if ccn >= _CCN_THRESHOLD:
            hits.append(
                PerfHit(
                    kind="sql_high_complexity",
                    line=start_line,
                    function=name,
                    detail=str(ccn),
                    func_start=start_line,
                )
            )
    return functions, hits


# ---------------------------------------------------------------------------
# Statement smells (AST tier)
# ---------------------------------------------------------------------------

# CREATE kinds whose body is a maintained relation (the ``select_star`` gate):
# ``SELECT *`` in an ad-hoc script is exploration, in a view it is a schema
# time bomb (column additions silently change the view's shape).
_STAR_SCOPES = frozenset({"VIEW", "MATERIALIZED VIEW", "FUNCTION", "PROCEDURE"})

# A DML/join target must be a clean identifier for its statement's parse to be
# trusted. When a dialect mismatch garbles the tokenization (a backtick-quoted
# MySQL table read under the permissive default yields a name of ``\```), the
# WHERE detection is equally garbled, so the smell stays silent, never guesses.
_CLEAN_IDENT_RE = re.compile(r"^[A-Za-z_][\w$]*$")


def _clean_ident(raw: str) -> str | None:
    token = raw.strip().strip('"`[]')
    return token if _CLEAN_IDENT_RE.match(token) else None


def _locate(text: str, token: str, cursor: int) -> tuple[int, int]:
    """First whole-word occurrence of *token* at/after *cursor* (forward-only
    cursor, statements arrive in source order). Falls back to line 1."""
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    match = pattern.search(text, cursor) or pattern.search(text)
    if match is None:
        return 1, cursor
    return _line_of(text, match.start()), match.end()


def _statement_smells(text: str, dialect: str | None) -> list[PerfHit]:
    # dbt/Jinja templates parse unpredictably (placeholder braces land in the
    # AST as junk nodes); a templated model is not a maintained DDL file, so
    # the statement smells stay silent there rather than guess.
    if "{{" in text or "{%" in text:
        return []
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    try:
        statements = sqlglot.parse(text, read=dialect, error_level=sqlglot.ErrorLevel.IGNORE)
    except Exception:
        return []

    hits: list[PerfHit] = []
    cursor = 0

    def _anchor(token: str) -> int:
        nonlocal cursor
        line, cursor = _locate(text, token, cursor)
        return line

    for stmt in statements:
        if stmt is None:
            continue

        # -- sql_select_star: bare * projection inside a maintained relation.
        if isinstance(stmt, exp.Create) and (stmt.kind or "").upper() in _STAR_SCOPES:
            name = _create_name(stmt, exp)
            for sel in stmt.find_all(exp.Select):
                if any(isinstance(e, exp.Star) for e in sel.expressions):
                    hits.append(
                        PerfHit(
                            kind="sql_select_star",
                            line=_anchor(name) if name else 1,
                            function=name,
                            detail=(stmt.kind or "").lower(),
                        )
                    )
                    break  # one finding per relation, not per nested select

        # -- sql_update_delete_without_where: whole-table DML.
        # A LIMIT (``UPDATE ... LIMIT n`` / ``DELETE ... LIMIT n`` in MySQL and
        # friends) bounds the affected row count, so the statement provably does
        # NOT touch every row — treat it like a WHERE and stay silent.
        elif (
            isinstance(stmt, (exp.Update, exp.Delete))
            and not stmt.args.get("where")
            and not stmt.args.get("limit")
        ):
            table = _clean_ident(getattr(stmt.this, "name", "") or "")
            if table:
                hits.append(
                    PerfHit(
                        kind="sql_update_delete_without_where",
                        line=_anchor(table),
                        function=None,
                        detail=f"{type(stmt).__name__.lower()} {table}",
                    )
                )

        # -- sql_cartesian_join: comma-join with no predicate anywhere.
        for sel in stmt.find_all(exp.Select):
            if sel.args.get("where"):
                continue
            for join in sel.args.get("joins") or ():
                if (
                    not join.args.get("kind")  # CROSS/OUTER etc. are explicit intent
                    and not join.args.get("side")
                    and not join.args.get("on")
                    and not join.args.get("using")
                    and not join.args.get("method")  # NATURAL carries its predicate
                ):
                    right = _clean_ident(getattr(join.this, "name", "") or "")
                    if right is None:
                        continue
                    hits.append(
                        PerfHit(
                            kind="sql_cartesian_join",
                            line=_anchor(right),
                            function=None,
                            detail=right,
                        )
                    )
                    break  # one finding per select
    return hits


def _create_name(stmt: Any, exp: Any) -> str | None:
    target = stmt.this
    if isinstance(target, exp.Schema):
        target = target.this
    if isinstance(target, exp.UserDefinedFunction):
        target = target.this
    return getattr(target, "name", "") or None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def walk_sql_file(file_info: Any, source: bytes) -> FileComplexity:
    """Health-walk one ``.sql`` file. Any failure degrades to the empty
    ``FileComplexity`` the tree-sitter walker returns for unmapped languages -
    no signal, never a crash."""
    text = source.decode("utf-8", errors="replace")
    file_nloc = _count_file_nloc(source)
    try:
        from repowise.core.ingestion.special_handlers import _sql_dialect_for

        dialect = _sql_dialect_for(file_info)
    except Exception:
        dialect = None
    try:
        functions, routine_hits = _walk_routines(text)
        smell_hits = _statement_smells(text, dialect)
    except Exception:
        return FileComplexity(functions=[], classes=[], file_nloc=file_nloc)
    return FileComplexity(
        functions=functions,
        classes=[],
        file_nloc=file_nloc,
        perf_hits=[*routine_hits, *smell_hits],
    )
