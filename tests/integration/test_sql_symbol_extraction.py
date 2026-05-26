"""Test SQL symbol extraction via sqlglot parser."""

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.special_handlers.sql import parse_sql_file


def _make_file_info(path: str, language: str = "sql") -> FileInfo:
    """Helper to create a minimal FileInfo for testing."""
    return FileInfo(
        path=path,
        abs_path=f"/fake/{path}",
        language=language,
        size_bytes=100,
        git_hash="abc123",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def test_sql_symbol_extraction_basic():
    """Test that SQL symbols are extracted from CREATE statements."""
    # Create test SQL file
    sql_file = Path("/tmp/test_basic.sql")
    sql_file.write_text("""
        CREATE TABLE [dbo].[Users](
            [UserId] INT PRIMARY KEY,
            [Email] NVARCHAR(256)
        );

        CREATE VIEW [dbo].[ActiveUsers]
        AS
        SELECT UserId, Email FROM dbo.Users;

        CREATE PROCEDURE [dbo].[GetUserByEmail]
            @Email NVARCHAR(256)
        AS
        SELECT * FROM dbo.Users WHERE Email = @Email;

        CREATE FUNCTION [dbo].[FormatEmail]
            (@Email NVARCHAR(256))
        RETURNS NVARCHAR(256)
        AS
        BEGIN
            RETURN LOWER(@Email);
        END;

        CREATE TRIGGER [dbo].[trg_Users_Audit]
        ON [dbo].[Users]
        AFTER INSERT
        AS
        PRINT 'Audit';
    """)

    # Parse file
    file_info = _make_file_info("test_basic.sql")
    parsed = parse_sql_file(file_info, sql_file.read_bytes())

    # Assert symbols extracted
    assert len(parsed.symbols) == 5, f"Expected 5 symbols, got {len(parsed.symbols)}: {[s.name for s in parsed.symbols]}"

    # Check table symbol
    table_symbols = [s for s in parsed.symbols if s.kind == "struct"]
    assert len(table_symbols) == 1
    assert table_symbols[0].name == "dbo.Users"

    # Check function symbols (VIEW + PROCEDURE + FUNCTION)
    function_symbols = [s for s in parsed.symbols if s.kind == "function"]
    assert len(function_symbols) == 3
    function_names = {s.name for s in function_symbols}
    assert "dbo.ActiveUsers" in function_names
    assert "dbo.GetUserByEmail" in function_names
    assert "dbo.FormatEmail" in function_names

    # Check trigger symbol
    trigger_symbols = [s for s in parsed.symbols if s.kind == "method"]
    assert len(trigger_symbols) == 1
    assert trigger_symbols[0].name == "dbo.trg_Users_Audit"


def test_sql_bracket_stripping():
    """Test that bracket stripping works correctly."""
    test_cases = [
        ("CREATE TABLE [dbo].[Users] (Id INT);", "dbo.Users"),
        ("CREATE TABLE dbo.Users (Id INT);", "dbo.Users"),
        ("CREATE VIEW [dbo].[ActiveUsers] AS SELECT 1;", "dbo.ActiveUsers"),
        ("CREATE PROCEDURE [dbo].[spTest] AS SELECT 1;", "dbo.spTest"),
        ("CREATE FUNCTION [dbo].[fnTest]() RETURNS INT AS BEGIN RETURN 1; END;", "dbo.fnTest"),
        ("CREATE TRIGGER [dbo].[trTest] ON [dbo].[Users] AFTER INSERT AS PRINT 1;", "dbo.trTest"),
    ]

    for sql, expected_name in test_cases:
        file_info = _make_file_info(f"test_{expected_name.replace('.', '_')}.sql")
        parsed = parse_sql_file(file_info, sql.encode())

        if expected_name in [s.name for s in parsed.symbols]:
            continue  # Found expected symbol
        else:
            assert False, f"Failed to extract '{expected_name}' from: {sql}"


def test_sql_schema_defaulting():
    """Test that implicit schema defaults to dbo for T-SQL."""
    sql = """
        CREATE TABLE Users (
            UserId INT PRIMARY KEY
        );

        CREATE VIEW ActiveUsers AS
        SELECT UserId FROM Users;
    """

    file_info = _make_file_info("test_defaulting.sql")
    parsed = parse_sql_file(file_info, sql.encode())

    # Check that symbols have dbo schema
    symbol_names = {s.name for s in parsed.symbols}
    assert "dbo.Users" in symbol_names
    assert "dbo.ActiveUsers" in symbol_names


def test_sql_full_fixture():
    """Test extraction from comprehensive T-SQL fixture."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "sql" / "schema.sql"

    if not fixture_path.exists():
        pytest.skip("SQL fixture not found")

    file_info = FileInfo(
        path=str(fixture_path.relative_to(Path(__file__).parent.parent.parent)),
        abs_path=str(fixture_path.absolute()),
        language="sql",
        size_bytes=fixture_path.stat().st_size,
        git_hash="abc123",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )

    parsed = parse_sql_file(file_info, fixture_path.read_bytes())

    # Expected symbols (excluding INDEX):
    # - 3 TABLE: Users, Posts, Tags
    # - 2 VIEW: ActiveUsers, RecentPosts
    # - 2 PROCEDURE: GetUserByEmail, CreatePost
    # - 2 FUNCTION: FormatEmail, GetUserPosts
    # - 1 TRIGGER: trg_Users_Audit
    # Total: 10 symbols (INDEX filtered out)

    assert len(parsed.symbols) >= 10, f"Expected at least 10 symbols, got {len(parsed.symbols)}"

    # Verify zero parse errors for supported syntax
    assert len(parsed.parse_errors) == 0, f"Parse errors: {parsed.parse_errors}"


def test_sql_symbol_kind_mapping():
    """Test that SQL kinds map to correct RepoWise SymbolKinds."""
    sql = """
        CREATE TABLE dbo.Users (Id INT);
        CREATE VIEW dbo.TestView AS SELECT 1;
        CREATE PROCEDURE dbo.spTest AS SELECT 1;
        CREATE FUNCTION dbo.fnTest() RETURNS INT AS BEGIN RETURN 1; END;
        CREATE TRIGGER dbo.trTest ON dbo.Users AFTER INSERT AS PRINT 1;
    """

    file_info = _make_file_info("test_kinds.sql")
    parsed = parse_sql_file(file_info, sql.encode())

    kind_counts = {}
    for symbol in parsed.symbols:
        kind_counts[symbol.kind] = kind_counts.get(symbol.kind, 0) + 1

    assert kind_counts.get("struct") == 1, "Should have 1 TABLE (struct)"
    assert kind_counts.get("function") == 3, "Should have 3 functions (VIEW + PROCEDURE + FUNCTION)"
    assert kind_counts.get("method") == 1, "Should have 1 TRIGGER (method)"
