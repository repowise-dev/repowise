"""SQL special handler using sqlglot parser.

Handles: CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX
Dialects: T-SQL (primary), PostgreSQL, MySQL (via sqlglot)
"""
from __future__ import annotations

import re
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
        symbols = _extract_symbols(ast, source_str, file_info)

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


def _extract_from_table_node(statement) -> str | None:
    """Extract table name from CREATE TABLE AST node.

    Args:
        statement: sqlglot CREATE TABLE node

    Returns:
        Fully qualified table name (schema.table) or None
    """
    if not hasattr(statement, "this"):
        return None

    this = statement.this
    if not hasattr(this, "this"):
        return None

    table = this.this
    schema = table.db if hasattr(table, "db") else None
    name = table.this

    # Extract string from Identifier nodes
    if schema and hasattr(schema, "this"):
        schema_str = schema.this
    else:
        schema_str = None

    if hasattr(name, "this"):
        name_str = name.this
    else:
        name_str = None

    if schema_str and name_str:
        return f"{schema_str}.{name_str}"
    elif name_str:
        return name_str
    return None


def _extract_from_procedure_node(statement) -> str | None:
    """Extract procedure name from CREATE PROCEDURE AST node.

    Args:
        statement: sqlglot CREATE PROCEDURE node

    Returns:
        Fully qualified procedure name (schema.procedure) or None
    """
    if not hasattr(statement, "this"):
        return None

    this = statement.this
    if not hasattr(this, "this"):
        return None

    procedure = this.this
    schema = procedure.db if hasattr(procedure, "db") else None
    name = procedure.this

    # Extract string from Identifier nodes
    if schema and hasattr(schema, "this"):
        schema_str = schema.this
    else:
        schema_str = None

    if hasattr(name, "this"):
        name_str = name.this
    else:
        name_str = None

    if schema_str and name_str:
        return f"{schema_str}.{name_str}"
    elif name_str:
        return name_str
    return None


def _extract_from_index_node(statement) -> str | None:
    """Extract index name from CREATE INDEX AST node.

    Args:
        statement: sqlglot CREATE INDEX node

    Returns:
        Index name or None
    """
    if not hasattr(statement, "this"):
        return None

    index = statement.this
    if hasattr(index, "this"):
        # Index is an Identifier, get the name
        return index.this
    return None


def _extract_from_regex(sql: str, kind: str) -> str | None:
    """Extract symbol name using regex fallback.

    Used for VIEW, FUNCTION, TRIGGER where sqlglot AST is complex.

    Args:
        sql: SQL statement string
        kind: Expected symbol kind

    Returns:
        Extracted name or None
    """
    # Pattern to match: CREATE {kind} [schema.]name
    patterns = [
        (r"CREATE\s+(?:VIEW|FUNCTION|TRIGGER)\s+\[?(\w+(?:\]\.\[\w+)*)\[?", "symbol"),
    ]

    for pattern, extract_type in patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            identifier = match.group(1)
            # Strip brackets and trailing parens
            identifier = identifier.replace("[", "").replace("]", "")
            identifier = re.sub(r"\(.*", "", identifier)
            return identifier

    return None


def _extract_symbols(ast, source: str, file_info: FileInfo) -> list[Symbol]:
    """Extract symbols from sqlglot AST.

    Strategy:
    1. AST walking for clean parses (TABLE, PROCEDURE, INDEX)
    2. Regex fallback for complex statements (VIEW, FUNCTION, TRIGGER)
    3. Schema defaulting: implicit → dbo (T-SQL)

    Args:
        ast: sqlglot AST
        source: SQL source string
        file_info: File metadata

    Returns:
        List of Symbol objects
    """
    symbols = []

    # Iterate through CREATE statements
    for statement in ast:
        if not hasattr(statement, "kind"):
            continue

        kind = statement.kind
        name = None
        params = ""

        # AST-based extraction for clean parses
        if kind == "TABLE":
            name = _extract_from_table_node(statement)
        elif kind == "PROCEDURE":
            name = _extract_from_procedure_node(statement)
        elif kind == "INDEX":
            name = _extract_from_index_node(statement)
        else:
            # Regex fallback for VIEW, FUNCTION, TRIGGER
            name = _extract_from_regex(statement.sql, kind)

        if name:
            # Apply transformations
            name = _strip_brackets(name)
            name = _default_schema(name, dialect="tsql")
            symbol_kind = _map_to_symbol_kind(kind)

            if symbol_kind:  # Skip INDEX (kind=None)
                # Extract line number
                line = statement.meta.get("start_line", 0) if hasattr(statement, "meta") else 0

                symbols.append(Symbol(
                    id=f"{file_info.path}::{name}",
                    name=name,
                    qualified_name=f"{file_info.path}.{name}",
                    kind=symbol_kind,
                    signature=params,
                    start_line=line + 1,
                    end_line=line + 1,
                    docstring=None,
                    decorators=[],
                    visibility="public",
                    is_async=False,
                    language="sql",
                    parent_name=None,
                    is_exported_symbol=False,
                ))

    return symbols


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
