"""Special handlers for non-tree-sitter file formats.

These parsers use plain text/regex/YAML parsing rather than tree-sitter because
the formats are simple enough (Dockerfile, Makefile) or require domain-specific
libraries (OpenAPI via PyYAML).

Each handler produces a fully-populated ParsedFile — the same output model as
the tree-sitter parsers — so the rest of the pipeline treats them identically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import structlog

from .models import FileInfo, Import, ParsedFile, Symbol

log = structlog.get_logger(__name__)


# Language tags routed to parse_special by the parser. Kept here, next to
# the handler table, so the two can't drift apart.
SPECIAL_HANDLER_LANGUAGES = frozenset({"openapi", "dockerfile", "makefile", "sql"})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_special(file_info: FileInfo, source: bytes, lang: str) -> ParsedFile:
    """Route to the correct special handler based on language tag."""
    handler: Callable[[FileInfo, bytes], ParsedFile] = {
        "openapi": _parse_openapi,
        "dockerfile": _parse_dockerfile,
        "makefile": _parse_makefile,
        "sql": _parse_sql,
    }.get(lang, _parse_unknown)
    try:
        return handler(file_info, source)
    except Exception as exc:
        log.warning("Special handler failed", path=file_info.path, error=str(exc))
        return _empty(file_info, parse_errors=[str(exc)])


# ---------------------------------------------------------------------------
# OpenAPI handler
# ---------------------------------------------------------------------------


def _parse_openapi(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Parse OpenAPI 2 / 3 YAML or JSON specs."""
    try:
        import yaml  # pyyaml, already in dependencies
    except ImportError:
        return _empty(file_info, parse_errors=["pyyaml not installed"])

    try:
        data = yaml.safe_load(source.decode("utf-8", errors="replace"))
    except Exception as exc:
        return _empty(file_info, parse_errors=[f"YAML parse error: {exc}"])

    if not isinstance(data, dict):
        return _empty(file_info, parse_errors=["Not a YAML mapping"])

    # Confirm it's an OpenAPI/Swagger spec
    if "openapi" not in data and "swagger" not in data:
        return _empty(file_info, parse_errors=["Not an OpenAPI/Swagger spec"])

    symbols: list[Symbol] = []
    _title = (data.get("info") or {}).get("title", file_info.path)

    paths = data.get("paths") or {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            if method.lower() in ("get", "post", "put", "patch", "delete", "head", "options"):
                op_id = (spec or {}).get("operationId", f"{method.upper()} {path}")
                summary = (spec or {}).get("summary")
                symbols.append(
                    Symbol(
                        id=f"{file_info.path}::{op_id}",
                        name=op_id,
                        qualified_name=op_id,
                        kind="function",
                        signature=f"{method.upper()} {path}",
                        start_line=1,
                        end_line=1,
                        docstring=summary,
                        visibility="public",
                        language="openapi",
                    )
                )

    # Components / schemas as type symbols
    components = (data.get("components") or {}).get("schemas") or (data.get("definitions") or {})
    for schema_name in components:
        symbols.append(
            Symbol(
                id=f"{file_info.path}::{schema_name}",
                name=schema_name,
                qualified_name=schema_name,
                kind="type_alias",
                signature=f"schema {schema_name}",
                start_line=1,
                end_line=1,
                docstring=None,
                visibility="public",
                language="openapi",
            )
        )

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        docstring=str(data.get("info", {}).get("description", "")) or None,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# Dockerfile handler
# ---------------------------------------------------------------------------

_FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.IGNORECASE)
_COPY_RE = re.compile(r"^\s*COPY\s+", re.IGNORECASE)
_RUN_RE = re.compile(r"^\s*RUN\s+", re.IGNORECASE)
_ENTRYPOINT_RE = re.compile(r"^\s*(?:ENTRYPOINT|CMD)\s+(.+)", re.IGNORECASE)
_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(\d+)", re.IGNORECASE)
_ENV_RE = re.compile(r"^\s*ENV\s+(\w+)", re.IGNORECASE)
_ARG_RE = re.compile(r"^\s*ARG\s+(\w+)", re.IGNORECASE)


def _parse_dockerfile(file_info: FileInfo, source: bytes) -> ParsedFile:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    imports: list[Import] = []
    symbols: list[Symbol] = []

    for lineno, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        # FROM → import
        m = _FROM_RE.match(line)
        if m:
            image = m.group(1)
            imports.append(
                Import(
                    raw_statement=line.strip(),
                    module_path=image,
                    imported_names=[image],
                    is_relative=False,
                    resolved_file=None,
                )
            )
            continue

        # ENTRYPOINT / CMD → entry-point symbol
        m = _ENTRYPOINT_RE.match(line)
        if m:
            name = "entrypoint" if "ENTRYPOINT" in line.upper() else "cmd"
            symbols.append(
                Symbol(
                    id=f"{file_info.path}::{name}",
                    name=name,
                    qualified_name=name,
                    kind="function",
                    signature=line.strip(),
                    start_line=lineno,
                    end_line=lineno,
                    docstring=None,
                    visibility="public",
                    language="dockerfile",
                )
            )
            continue

        # EXPOSE → constant
        m = _EXPOSE_RE.match(line)
        if m:
            port = m.group(1)
            symbols.append(
                Symbol(
                    id=f"{file_info.path}::EXPOSE_{port}",
                    name=f"EXPOSE_{port}",
                    qualified_name=f"port_{port}",
                    kind="constant",
                    signature=line.strip(),
                    start_line=lineno,
                    end_line=lineno,
                    docstring=None,
                    visibility="public",
                    language="dockerfile",
                )
            )

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=imports,
        exports=[],
        docstring=None,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# Makefile handler
# ---------------------------------------------------------------------------

# Matches: target_name: [prerequisites...]
_TARGET_RE = re.compile(r"^([a-zA-Z0-9_][a-zA-Z0-9_\-./]*):[^=]")
_INCLUDE_RE = re.compile(r"^include\s+(.+)", re.IGNORECASE)
_PHONY_RE = re.compile(r"^\.PHONY\s*:\s*(.+)")


def _parse_makefile(file_info: FileInfo, source: bytes) -> ParsedFile:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    symbols: list[Symbol] = []
    imports: list[Import] = []
    phony_targets: set[str] = set()

    # First pass: collect .PHONY targets
    for line in lines:
        m = _PHONY_RE.match(line)
        if m:
            phony_targets.update(m.group(1).split())

    # Second pass: extract targets
    for lineno, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        m = _TARGET_RE.match(line)
        if m:
            target = m.group(1)
            if not target.startswith("."):  # skip .PHONY, .SUFFIXES, etc.
                symbols.append(
                    Symbol(
                        id=f"{file_info.path}::{target}",
                        name=target,
                        qualified_name=target,
                        kind="function",
                        signature=f"{target}:",
                        start_line=lineno,
                        end_line=lineno,
                        docstring=None,
                        visibility="public",
                        language="makefile",
                    )
                )
            continue

        m = _INCLUDE_RE.match(line)
        if m:
            include_path = m.group(1).strip()
            imports.append(
                Import(
                    raw_statement=line.strip(),
                    module_path=include_path,
                    imported_names=[],
                    is_relative=True,
                    resolved_file=None,
                )
            )

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=imports,
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# SQL handler (sqlglot)
# ---------------------------------------------------------------------------

# sqlglot Create.kind → repowise SymbolKind. Tables and views are the
# "classes" of a schema (columns in the signature); routines are functions.
# INDEX / TRIGGER / SCHEMA / DATABASE are deliberately not symbols.
_SQL_SYMBOL_KINDS = {
    "TABLE": "class",
    "VIEW": "class",
    "MATERIALIZED VIEW": "class",
    "FUNCTION": "function",
    "PROCEDURE": "function",
}

_SIGNATURE_MAX = 300


def _parse_sql(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Parse .sql files: dbt ref()/source() imports plus DDL symbols.

    Imports come from the same regex extractor the lightweight tier uses,
    so dbt lineage survives even when sqlglot can't parse the file (dbt
    models are Jinja templates, so sqlglot failure is the expected case
    there, and models carry no DDL symbols anyway). Symbol extraction is
    best-effort: any parse problem degrades to the pre-handler passthrough
    behaviour (no symbols), never a crash.
    """
    text = source.decode("utf-8", errors="replace")

    from .lightweight_imports.sql import extract_dbt_imports

    imports = extract_dbt_imports(text)
    symbols, parse_errors = _extract_sql_symbols(file_info, text)

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=imports,
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=parse_errors,
    )


def _extract_sql_symbols(file_info: FileInfo, text: str) -> tuple[list[Symbol], list[str]]:
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return [], ["sqlglot not installed"]

    # sqlglot warns per statement it falls back to a generic Command for
    # (procedural bodies, vendor syntax); routine here, so keep it quiet.
    import logging

    logging.getLogger("sqlglot").setLevel(logging.ERROR)

    try:
        statements = sqlglot.parse(
            text,
            read=_sql_dialect_for(file_info),
            error_level=sqlglot.ErrorLevel.IGNORE,
        )
    except Exception as exc:
        # Tokenizer errors and dialect-specific syntax sqlglot can't
        # recover from: degrade the file to passthrough behaviour.
        return [], [f"SQL parse error: {exc}"]

    symbols: list[Symbol] = []
    cursor = 0
    for stmt in statements:
        if not isinstance(stmt, exp.Create):
            continue
        kind = _SQL_SYMBOL_KINDS.get((stmt.kind or "").upper())
        if kind is None:
            continue

        target = stmt.this
        schema_node = target if isinstance(target, exp.Schema) else None
        name_node = schema_node.this if schema_node is not None else target
        if isinstance(name_node, exp.UserDefinedFunction):
            name_node = name_node.this  # CREATE FUNCTION wraps a Table node
        name = getattr(name_node, "name", "") or ""
        if not name:
            continue
        db = getattr(name_node, "db", "") or ""
        qualified = f"{db}.{name}" if db else name

        columns = (
            [c.name for c in schema_node.expressions if isinstance(c, exp.ColumnDef)]
            if schema_node is not None
            else []
        )
        kind_text = (stmt.kind or "").upper()
        signature = f"{kind_text} {qualified}"
        if columns:
            signature = f"{signature}({', '.join(columns)})"

        line, cursor = _locate_line(text, name, cursor)
        symbols.append(
            Symbol(
                id=f"{file_info.path}::{qualified}",
                name=name,
                qualified_name=qualified,
                kind=kind,  # type: ignore[arg-type]
                signature=signature[:_SIGNATURE_MAX],
                start_line=line,
                end_line=line,
                docstring=None,
                visibility="public",
                language="sql",
            )
        )

    return symbols, []


def _locate_line(text: str, name: str, cursor: int) -> tuple[int, int]:
    """Best-effort line number: first whole-word occurrence of *name* at or
    after *cursor* (statements arrive in source order, so the cursor only
    moves forward). Falls back to line 1, matching the OpenAPI handler."""
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    match = pattern.search(text, cursor) or pattern.search(text)
    if match is None:
        return 1, cursor
    return text.count("\n", 0, match.start()) + 1, match.end()


@lru_cache(maxsize=64)
def _read_sql_dialect(repo_root: str) -> str | None:
    """``sql_dialect`` from .repowise/config.yaml, validated against
    sqlglot's dialect registry. None → sqlglot's permissive default."""
    try:
        from ..repo_config import load_repo_config

        value = load_repo_config(Path(repo_root)).get("sql_dialect")
        if not value:
            return None
        from sqlglot.dialects.dialect import Dialect

        if Dialect.get(str(value).lower()) is None:
            log.warning("Unknown sql_dialect in config.yaml, ignoring", sql_dialect=value)
            return None
        return str(value).lower()
    except Exception:
        return None


def _sql_dialect_for(file_info: FileInfo) -> str | None:
    abs_posix = file_info.abs_path.replace("\\", "/")
    if abs_posix.endswith("/" + file_info.path):
        return _read_sql_dialect(abs_posix[: -len(file_info.path) - 1])
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_unknown(file_info: FileInfo, source: bytes) -> ParsedFile:
    return _empty(file_info, parse_errors=[f"No special handler for {file_info.language}"])


def _empty(file_info: FileInfo, parse_errors: list[str] | None = None) -> ParsedFile:
    return ParsedFile(
        file_info=file_info,
        symbols=[],
        imports=[],
        exports=[],
        docstring=None,
        parse_errors=parse_errors or [],
    )
