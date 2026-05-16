"""SQL special handler using sqlglot parser.

Handles: CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX
Dialects: T-SQL (primary), PostgreSQL, MySQL (via sqlglot)
"""
from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot.dialects import TSQL

from repowise.core.ingestion.models import FileInfo, ParsedFile, Symbol


def parse_sql_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Parse SQL file using sqlglot, extract symbols.

    Args:
        file_info: File metadata
        source: SQL source code bytes

    Returns:
        ParsedFile with extracted symbols
    """
    source_str = source.decode("utf-8", errors="replace")

    try:
        # Parse SQL with T-SQL dialect
        ast = sqlglot.parse(source_str, dialect=TSQL)

        # Extract symbols
        symbols = _extract_symbols(ast, source_str)

        # TODO: Implement parse_errors collection
        parse_errors = []

        return ParsedFile(
            file_info=file_info,
            symbols=symbols,
            imports=[],
            exports=[],
            calls=[],
            heritage=[],
            docstring=None,
            parse_errors=parse_errors,
        )

    except Exception as exc:
        # If parsing completely fails, return empty ParsedFile
        return ParsedFile(
            file_info=file_info,
            symbols=[],
            imports=[],
            exports=[],
            calls=[],
            heritage=[],
            docstring=None,
            parse_errors=[f"SQL parsing failed: {exc}"],
        )


def _extract_symbols(ast, source: str) -> list[Symbol]:
    """Extract symbols from sqlglot AST.

    Strategy:
    1. AST walking for clean parses (TABLE, PROCEDURE, INDEX)
    2. Regex fallback for complex statements (VIEW, FUNCTION, TRIGGER)
    3. Schema defaulting: implicit → dbo (T-SQL)

    Args:
        ast: sqlglot AST
        source: SQL source string

    Returns:
        List of Symbol objects
    """
    # TODO: Implement in Task 3
    return []


def _strip_brackets(name: str) -> str:
    """Strip SQL identifier quoting.

    T-SQL: [dbo].[Users] → dbo.Users
    MySQL: `dbo`.`Users` → dbo.Users
    PostgreSQL: "dbo"."Users" → dbo.Users
    """
    return name.replace("[", "").replace("]", "").replace("`", "").replace('"', "")


def _default_schema(name: str, dialect: str = "tsql") -> str:
    """Default schema when implicit.

    T-SQL: Users → dbo.Users
    """
    if "." not in name:
        default = "dbo" if dialect == "tsql" else "public"
        return f"{default}.{name}"
    return name


def _map_to_symbol_kind(sql_kind: str) -> str | None:
    """Map SQL CREATE kind to RepoWise SymbolKind.

    Args:
        sql_kind: sqlglot kind (TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX)

    Returns:
        RepoWise SymbolKind or None (for INDEX)
    """
    kind_map = {
        "TABLE": "struct",
        "VIEW": "function",
        "PROCEDURE": "function",
        "FUNCTION": "function",
        "TRIGGER": "method",
        "INDEX": None,  # INDEX captured but no SymbolKind (PR2)
    }
    return kind_map.get(sql_kind)
