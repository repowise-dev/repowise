"""Unit tests for the SQL special handler (sqlglot symbol extraction).

Covers DDL symbol mapping per major dialect flavour, graceful degradation
on malformed input, dbt-model Jinja tolerance, the ``sql_dialect`` config
key, and the parse_file routing that sends .sql (and the other special
formats) to their handlers.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.special_handlers import parse_special


def _file_info(rel: str, lang: str, root: str = "/repo") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=f"{root}/{rel}",
        language=lang,  # type: ignore[arg-type]
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _symbols(source: str, rel: str = "schema.sql", root: str = "/repo"):
    parsed = parse_special(_file_info(rel, "sql", root), source.encode(), "sql")
    return parsed


class TestSqlSymbols:
    def test_create_table_with_columns(self) -> None:
        parsed = _symbols("CREATE TABLE users (\n  id INT PRIMARY KEY,\n  email VARCHAR(255)\n);")
        assert len(parsed.symbols) == 1
        sym = parsed.symbols[0]
        assert sym.name == "users"
        assert sym.kind == "class"
        assert sym.signature == "TABLE users(id, email)"
        assert sym.start_line == 1
        assert parsed.exports == ["users"]

    def test_schema_qualified_table(self) -> None:
        parsed = _symbols("CREATE TABLE public.orders (id SERIAL, total NUMERIC);")
        sym = parsed.symbols[0]
        assert sym.qualified_name == "public.orders"
        assert sym.id.endswith("::public.orders")

    def test_views_and_materialized_views(self) -> None:
        parsed = _symbols(
            "CREATE VIEW active_users AS SELECT 1;\nCREATE MATERIALIZED VIEW mv_daily AS SELECT 2;"
        )
        assert [(s.name, s.kind) for s in parsed.symbols] == [
            ("active_users", "class"),
            ("mv_daily", "class"),
        ]
        assert parsed.symbols[1].start_line == 2

    def test_tsql_procedure(self) -> None:
        parsed = _symbols("CREATE PROCEDURE dbo.GetUsers AS BEGIN SELECT 1 END;")
        sym = parsed.symbols[0]
        assert sym.name == "GetUsers"
        assert sym.qualified_name == "dbo.GetUsers"
        assert sym.kind == "function"

    def test_index_and_trigger_are_not_symbols(self) -> None:
        parsed = _symbols("CREATE TABLE t (a INT);\nCREATE INDEX idx_a ON t(a);")
        assert [s.name for s in parsed.symbols] == ["t"]

    def test_multiple_statements_keep_line_numbers(self) -> None:
        parsed = _symbols("-- schema\nCREATE TABLE a (x INT);\n\nCREATE TABLE b (y INT);\n")
        assert [(s.name, s.start_line) for s in parsed.symbols] == [("a", 2), ("b", 4)]

    def test_malformed_sql_degrades_gracefully(self) -> None:
        parsed = _symbols("CREATE TABLE (((((;;; garbage !!")
        assert parsed.symbols == []
        # No crash is the contract; error reporting is best-effort.

    def test_dml_only_file_has_no_symbols(self) -> None:
        parsed = _symbols("INSERT INTO t VALUES (1);\nUPDATE t SET x = 2;")
        assert parsed.symbols == []
        assert parsed.parse_errors == []


class TestSqlDialectConfig:
    def test_postgres_function_with_configured_dialect(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "config.yaml").write_text(
            "sql_dialect: postgres\n", encoding="utf-8"
        )
        source = "CREATE FUNCTION add_one(x INT) RETURNS INT AS $$ SELECT x + 1 $$ LANGUAGE sql;"
        parsed = _symbols(source, root=tmp_path.as_posix())
        assert [(s.name, s.kind) for s in parsed.symbols] == [("add_one", "function")]

    def test_unknown_dialect_falls_back_to_default(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "config.yaml").write_text(
            "sql_dialect: not_a_dialect\n", encoding="utf-8"
        )
        parsed = _symbols("CREATE TABLE t (a INT);", root=tmp_path.as_posix())
        assert [s.name for s in parsed.symbols] == ["t"]


class TestDbtModelsThroughHandler:
    def test_jinja_model_keeps_imports_and_never_crashes(self) -> None:
        source = (
            "with orders as (\n"
            "    select * from {{ ref('stg_orders') }}\n"
            "),\n"
            "payments as (\n"
            "    select * from {{ source('stripe', 'payment') }}\n"
            ")\n"
            "select * from orders join payments using (order_id)\n"
        )
        parsed = _symbols(source, rel="models/orders.sql")
        assert [i.module_path for i in parsed.imports] == [
            "stg_orders",
            "source:stripe.payment",
        ]
        assert parsed.symbols == []


class TestParserRouting:
    def test_sql_reaches_special_handler(self) -> None:
        parsed = ASTParser().parse_file(
            _file_info("db/schema.sql", "sql"), b"CREATE TABLE t (a INT);"
        )
        assert [s.name for s in parsed.symbols] == ["t"]

    def test_sql_dbt_imports_flow_through_parse_file(self) -> None:
        parsed = ASTParser().parse_file(
            _file_info("models/orders.sql", "sql"),
            b"select * from {{ ref('stg_orders') }}",
        )
        assert [i.module_path for i in parsed.imports] == ["stg_orders"]

    def test_dockerfile_reaches_special_handler(self) -> None:
        # Regression: the special-handler branch used to sit below the
        # no-grammar fallback and never fired for any of its languages.
        parsed = ASTParser().parse_file(
            _file_info("Dockerfile", "dockerfile"),
            b"FROM python:3.12\nEXPOSE 8000\n",
        )
        assert [i.module_path for i in parsed.imports] == ["python:3.12"]
        assert [s.name for s in parsed.symbols] == ["EXPOSE_8000"]

    def test_makefile_reaches_special_handler(self) -> None:
        parsed = ASTParser().parse_file(
            _file_info("Makefile", "makefile"),
            b"build: deps\n\tgo build ./...\n",
        )
        assert [s.name for s in parsed.symbols] == ["build"]
