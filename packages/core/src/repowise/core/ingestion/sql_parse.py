"""One guarded entry point for every ``sqlglot.parse`` call in repowise.

sqlglot's parser can loop forever, allocating a node per pass, on statements
it cannot consume: ``COPY t FROM :'f';`` (a psql variable), ``COPY t FROM -;``
or a column constraint typo such as ``id3 INTEGER NOT NUL`` never return,
under every error level and dialect. A thread cannot be stopped, so each
statement gets a node budget instead: a parse that builds far more nodes than
the statement has tokens is not making progress, and that statement alone is
dropped (``None``, like any statement sqlglot cannot read) while the rest of
the file parses as before.

psql backslash meta-commands (``\\set``, ``\\copy``, ``\\gset``) are client
syntax, not SQL, and run to the end of their line. Their tokens are dropped
before parsing so they do not swallow the statement that follows them.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

__all__ = ["drop_psql_meta_commands", "parse_sql"]

# Nodes one statement may build: a fixed floor plus a multiple of its tokens.
# Across the 22k statements of the postgres regression suite the most any
# statement built was 2.15 nodes per token and 471 in total, so this leaves a
# 10x margin, and a stuck loop still passes it within a few milliseconds.
_NODE_BUDGET_FLOOR = 1_000
_NODES_PER_TOKEN = 20


class _NodeBudgetError(Exception):
    """Raised inside the parser when a statement exhausts its node budget."""


def drop_psql_meta_commands(text: str, tokens: list[Any]) -> list[Any]:
    """*tokens* minus each psql meta-command: a backslash token to the end of its line.

    The tokenizer has already read string literals and comments, so a
    backslash inside one is part of that token and never starts a command.
    ``\\g``, ``\\gset``, ``\\gexec`` and the like send the query buffer, so
    they end a statement just as ``;`` does.
    """
    from sqlglot.tokens import Token, TokenType

    kept: list[Any] = []
    line_end = -1
    for tok in tokens:
        if tok.start < line_end:
            continue
        if tok.token_type == TokenType.BACKSLASH:
            line_end = text.find("\n", tok.start)
            if line_end == -1:
                line_end = len(text)
            if text.startswith("g", tok.start + 1):
                kept.append(
                    Token(TokenType.SEMICOLON, ";", tok.line, tok.col, tok.start, tok.start)
                )
            continue
        kept.append(tok)
    return kept


@lru_cache(maxsize=32)
def _budgeted(parser_class: type) -> type:
    """*parser_class* with a per-statement cap on the nodes it may build."""

    class BudgetedParser(parser_class):
        _node_budget = 0
        _in_statement = False

        def expression(self, exp_class: Any, comments: Any = None, **kwargs: Any) -> Any:
            self._node_budget -= 1
            if self._node_budget < 0:
                raise _NodeBudgetError
            return super().expression(exp_class, comments, **kwargs)

        def _parse_statement(self) -> Any:
            # Procedural bodies parse nested statements; only the top level
            # (one ``;``-separated chunk) sets the budget, so a loop that
            # re-enters here cannot refill it.
            if self._in_statement:
                return super()._parse_statement()
            self._in_statement = True
            self._node_budget = _NODE_BUDGET_FLOOR + _NODES_PER_TOKEN * len(self._tokens)
            try:
                return super()._parse_statement()
            except _NodeBudgetError:
                # Consume the rest of this statement so the next one starts clean.
                while self._curr:
                    self._advance()
                return None
            finally:
                self._in_statement = False

    return BudgetedParser


def parse_sql(
    text: str,
    *,
    dialect: str | None = None,
    error_level: Any = None,
    psql_meta_commands: bool = True,
) -> list[Any]:
    """``sqlglot.parse`` with a per-statement node budget.

    Raises ``ImportError`` when sqlglot is missing and whatever sqlglot raises
    otherwise, so each caller keeps its own degrade path. *error_level*
    defaults to ``ErrorLevel.IGNORE``. A statement over budget comes back as
    ``None``. *psql_meta_commands* drops psql's backslash commands first; it
    is for ``.sql`` files, not SQL embedded in code, where a stray backslash
    should still fail the parse.
    """
    import sqlglot
    from sqlglot.dialects.dialect import Dialect

    # sqlglot warns per statement it falls back to a generic Command for
    # (procedural bodies, vendor syntax); routine here, so keep it quiet.
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    level = sqlglot.ErrorLevel.IGNORE if error_level is None else error_level
    sql_dialect = Dialect.get_or_raise(dialect)
    tokens = sql_dialect.tokenize(text)
    if psql_meta_commands and "\\" in text:
        tokens = drop_psql_meta_commands(text, tokens)
    parser = _budgeted(sql_dialect.parser_class)(dialect=sql_dialect, error_level=level)
    return parser.parse(tokens, text)
