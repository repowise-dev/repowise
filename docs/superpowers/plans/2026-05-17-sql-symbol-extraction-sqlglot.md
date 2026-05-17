# SQL Symbol Extraction (sqlglot-based) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace tree-sitter-sql with sqlglot parser to extract SQL symbols (TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX) with proper bracket stripping and schema defaulting.

**Architecture:** Add sqlglot as special handler alongside tree-sitter. Route SQL files to sqlglot-based parser, keep tree-sitter for other languages. Extract symbols using AST walking + regex fallback.

**Tech Stack:** sqlglot>=30.0,<32, T-SQL dialect, existing RepoWise ingestion pipeline

---

## Files

**Create:**
- `packages/core/src/repowise/core/ingestion/special_handlers/sql.py` — sqlglot-based SQL parser
- `tests/unit/ingestion/test_sql_extraction.py` — unit tests for extraction logic

**Modify:**
- `pyproject.toml:36-52` — add sqlglot dependency, remove tree-sitter-sql
- `packages/core/src/repowise/core/ingestion/languages/registry.py:970-976` — add special_handler="sql" to LanguageSpec
- `packages/core/src/repowise/core/ingestion/parser.py:518-545` — route SQL to special handler
- `tests/fixtures/sql/schema.sql` — update with all 6 symbol types
- `tests/integration/test_sql_symbol_extraction.py` — comprehensive integration tests
- `docs/LANGUAGE_SUPPORT.md:73` — move SQL from Config/Data to Good tier

**Delete:**
- `packages/core/src/repowise/core/ingestion/queries/sql.scm` — no longer needed

---

## Task 1: Add sqlglot dependency

**Files:**
- Modify: `pyproject.toml:36-52`

- [ ] **Step 1: Read current dependencies section**

```bash
head -60 pyproject.toml
```

Expected: See tree-sitter dependencies starting at line 36

- [ ] **Step 2: Add sqlglot after tree-sitter-luau**

Insert after line 51 (tree-sitter-luau):
```toml
    "sqlglot>=30.0,<32",
```

- [ ] **Step 3: Remove tree-sitter-sql dependency**

Find and remove line: `"tree-sitter-sql>=0.3,<1",`

- [ ] **Step 4: Verify TOML syntax**

```bash
python3 -c "import tomllib; f = open('pyproject.toml', 'rb'); tomllib.load(f); print('TOML OK')"
```

Expected: No syntax errors

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "deps(sql): add sqlglot>=30.0,<32, remove tree-sitter-sql"
```

---

## Task 2: Create SQL special handler skeleton

**Files:**
- Create: `packages/core/src/repowise/core/ingestion/special_handlers/sql.py`

- [ ] **Step 1: Check special_handlers directory structure**

```bash
ls -la packages/core/src/repowise/core/ingestion/special_handlers/
```

Expected: See existing handlers (openapi.py, dockerfile.py, makefile.py)

- [ ] **Step 2: Create sql.py skeleton**

Create `packages/core/src/repowise/core/ingestion/special_handlers/sql.py`:
```python
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
    # TODO: Implement in Task 4
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
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -c "from packages.core.src.repowise.core.ingestion.special_handlers import sql; print('Import OK')"
```

Expected: Import OK

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/repowise/core/ingestion/special_handlers/sql.py
git commit -m "feat(sql): add sqlglot-based special handler skeleton"
```

---

## Task 3: Implement AST-based symbol extraction

**Files:**
- Modify: `packages/core/src/repowise/core/ingestion/special_handlers/sql.py`

- [ ] **Step 1: Implement _extract_symbols for AST-based extraction**

Replace the `_extract_symbols` function in `sql.py`:
```python
def _extract_symbols(ast, source: str) -> list[Symbol]:
    """Extract symbols from sqlglot AST.

    Strategy:
    1. AST walking for clean parses (TABLE, PROCEDURE, INDEX)
    2. Regex fallback for complex statements (VIEW, FUNCTION, TRIGGER)
    3. Schema defaulting: implicit → dbo (T-S-SQL)

    Args:
        ast: sqlglot AST
        source: SQL source string

    Returns:
        List of Symbol objects
    """
    import re

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
```

- [ ] **Step 2: Implement _extract_from_table_node**

Add function before `_extract_symbols`:
```python
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
```

- [ ] **Step 3: Implement _extract_from_procedure_node**

Add function after `_extract_from_table_node`:
```python
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
```

- [ ] **Step 4: Implement _extract_from_index_node**

Add function after `_extract_from_procedure_node`:
```python
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
        name = index.this if hasattr(index, "this") else None
        return name
    return None
```

- [ ] **Step 5: Implement _extract_from_regex fallback**

Add function after `_extract_from_index_node`:
```python
def _extract_from_regex(sql: str, kind: str) -> str | None:
    """Extract symbol name using regex fallback.

    Used for VIEW, FUNCTION, TRIGGER where sqlglot AST is complex.

    Args:
        sql: SQL statement string
        kind: Expected symbol kind

    Returns:
        Extracted name or None
    """
    import re

    # Pattern to match: CREATE {kind} [schema.]name
    patterns = [
        (rf"CREATE\s+(?:VIEW|FUNCTION|TRIGGER)\s+\[?(\[?\w+\]?\.\[?\w+\]?\[?\w+\]?)", "symbol"),
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
```

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/repowise/core/ingestion/special_handlers/sql.py
git commit -m "feat(sql): implement AST-based symbol extraction with regex fallback"
```

---

## Task 4: Wire up special handler in parser

**Files:**
- Modify: `packages/core/src/repowise/core/ingestion/parser.py:518-545`

- [ ] **Step 1: Read parser.py parse_file method**

```bash
sed -n '518,545p' packages/core/src/repowise/core/ingestion/parser.py
```

Expected: See the parse_file method with special_handlers check

- [ ] **Step 2: Add SQL routing before special_handlers check**

Find the line:
```python
# Delegate to special handlers for non-tree-sitter formats
if lang in ("openapi", "dockerfile", "makefile"):
```

Replace with:
```python
# Delegate to special handlers for non-tree-sitter formats
if lang == "sql":
    from .special_handlers import parse_sql_file
    return parse_sql_file(file_info, source)
elif lang in ("openapi", "dockerfile", "makefile"):
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -m py_compile packages/core/src/repowise/core/ingestion/parser.py && echo "Syntax OK"
```

Expected: Syntax OK

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/repowise/core/ingestion/parser.py
git commit -m "feat(sql): route SQL parsing to sqlglot special handler"
```

---

## Task 5: Update LanguageSpec to use special handler

**Files:**
- Modify: `packages/core/src/repowise/core/ingestion/languages/registry.py:970-976`

- [ ] **Step 1: Read current SQL LanguageSpec**

```bash
sed -n '970,976p' packages/core/src/repowise/core/ingestion/languages/registry.py
```

Expected: Current passthrough config

- [ ] **Step 2: Replace LanguageSpec to use special_handler**

Replace with:
```python
    LanguageSpec(
        tag="sql",
        display_name="SQL",
        extensions=frozenset({".sql"}),
        special_handler="sql",  # Uses sqlglot-based parser
        is_code=True,
        is_passthrough=False,
    ),
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -c "from packages.core.src.repowise.core.ingestion.languages.registry import REGISTRY; sql = [s for s in REGISTRY._specs if s.tag == 'sql'][0]; print(f'SQL tag: {sql.tag}, handler: {sql.special_handler}')"
```

Expected: `SQL tag: sql, handler: sql`

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/repise/core/ingestion/languages/registry.py
git commit -m "feat(sql): update LanguageSpec to use special_handler"
```

---

## Task 6: Update test fixture for full coverage

**Files:**
- Modify: `tests/fixtures/sql/schema.sql`

- [ ] **Step 1: Read current fixture**

```bash
cat tests/fixtures/sql/schema.sql
```

Expected: Existing T-SQL fixture with TABLE/VIEW

- [ ] **Step 2: Replace with comprehensive fixture**

Replace entire file content with:
```sql
-- =============================================================================
-- RepoWise SQL Symbol Extraction Test Fixture
-- T-SQL dialect (SQL Server)
-- Covers: CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX
-- =============================================================================

-- CREATE TABLE with schema qualification, brackets, constraints
CREATE TABLE [dbo].[Users](
    [UserId] INT IDENTITY(1,1) PRIMARY KEY,
    [Email] NVARCHAR(256) NOT NULL,
    [Created] DATETIME DEFAULT GETDATE()
);

-- CREATE TABLE without explicit schema (should default to dbo)
CREATE TABLE [Posts](
    [PostId] INT IDENTITY(1,1) PRIMARY KEY,
    [UserId] INT NOT NULL,
    [Content] NVARCHAR(MAX),
    [Published] DATETIME DEFAULT GETDATE(),
    FOREIGN KEY ([UserId]) REFERENCES [dbo].[Users]([UserId])
);

-- CREATE VIEW referencing base tables
CREATE VIEW [dbo].[ActiveUsers]
AS
SELECT UserId, Email FROM dbo.Users WHERE Created > DATEADD(day, -30, GETDATE());

-- CREATE VIEW without schema prefix
CREATE VIEW [RecentPosts]
AS
SELECT TOP 10 PostId, Content, Published FROM dbo.Posts ORDER BY Published DESC;

-- CREATE PROCEDURE with parameters
CREATE PROCEDURE [dbo].[GetUserByEmail]
    @Email NVARCHAR(256)
AS
SELECT * FROM dbo.Users WHERE Email = @Email;

-- CREATE PROCEDURE with multiple parameters
CREATE PROCEDURE [dbo].[CreatePost]
    @UserId INT,
    @Content NVARCHAR(MAX)
AS
INSERT INTO dbo.Posts (UserId, Content, Published) VALUES (@UserId, @Content, GETDATE());

-- CREATE FUNCTION (scalar)
CREATE FUNCTION [dbo].[FormatEmail]
    (@Email NVARCHAR(256))
RETURNS NVARCHAR(256)
AS
BEGIN
    RETURN LOWER(@Email);
END;

-- CREATE FUNCTION (table-valued)
CREATE FUNCTION [dbo].[GetUserPosts]
    (@UserId INT)
RETURNS TABLE
AS
RETURN
SELECT PostId, Content, Published FROM dbo.Posts WHERE UserId = @UserId;

-- CREATE TRIGGER
CREATE TRIGGER [dbo].[trg_Users_Audit]
ON [dbo].[Users]
AFTER INSERT, UPDATE
AS
BEGIN
    -- Audit logic would go here
    PRINT 'Users table modified';
END;

-- CREATE INDEX
CREATE INDEX [IX_Posts_Email] ON [dbo].[Posts]([Email]);

-- Schemaless table (no brackets, implicit dbo schema)
CREATE TABLE Tags (
    TagId INT IDENTITY(1,1) PRIMARY KEY,
    Name NVARCHAR(50) NOT NULL
);
```

- [ ] **Step 3: Verify fixture syntax**

```bash
python3 -c "
import sqlglot
from sqlglot.dialects import TSQL
content = open('tests/fixtures/sql/schema.sql').read()
ast = sqlglot.parse(content, dialect=TSQL)
print(f'Fixture parses with {len(ast)} statements')
" 2>&1 | grep -v "UserWarning"
```

Expected: Fixture parses with 12 statements

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/sql/schema.sql
git commit -m "test(sql): update fixture with all 6 symbol types"
```

---

## Task 7: Write integration tests

**Files:**
- Modify: `tests/integration/test_sql_symbol_extraction.py`

- [ ] **Step 1: Replace entire test file**

Replace `tests/integration/test_sql_symbol_extraction.py` with:
```python
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
```

- [ ] **Step 2: Verify test syntax**

```bash
python3 -m py_compile tests/integration/test_sql_symbol_extraction.py && echo "Syntax OK"
```

Expected: Syntax OK

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_sql_symbol_extraction.py
git commit -m "test(sql): add comprehensive integration tests for all 6 symbol types"
```

---

## Task 8: Update documentation

**Files:**
- Modify: `docs/LANGUAGE_SUPPORT.md:73`

- [ ] **Step 1: Read SQL entry in Config/Data section**

```bash
sed -n '90,95p' docs/LANGUAGE_SUPPORT.md
```

Expected: SQL listed in Config / Data section (line 93)

- [ ] **Step 2: Remove SQL from Config/Data section**

Remove line 93:
```markdown
| **SQL** | `.sql` | -- |
```

- [ ] **Step 3: Add SQL to Good section**

Add to Good section table after PHP (after line 73):
```markdown
| **SQL** | `.sql` | -- | No imports/heritage; sqlglot parser handles T-SQL, PostgreSQL, MySQL |
```

- [ ] **Step 4: Verify markdown syntax**

```bash
python3 -c "import markdown; markdown.markdown(open('docs/LANGUAGE_SUPPORT.md').read()); print('Markdown OK')"
```

Expected: No syntax errors

- [ ] **Step 5: Commit**

```bash
git add docs/LANGUAGE_SUPPORT.md
git commit -m "docs(sql): move SQL from Config/Data to Good tier with sqlglot parser"
```

---

## Task 9: Cleanup - Remove tree-sitter-sql artifacts

**Files:**
- Delete: `packages/core/src/repowise/core/ingestion/queries/sql.scm`

- [ ] **Step 1: Remove sql.scm query file**

```bash
rm packages/core/src/repowise/core/ingestion/queries/sql.scm
git rm packages/core/src/repowise/core/ingestion/queries/sql.scm
```

- [ ] **Step 2: Verify file deleted**

```bash
ls packages/core/src/repowise/core/ingestion/queries/sql.scm 2>&1 || echo "File successfully deleted"
```

Expected: "No such file or directory"

- [ ] **Step 3: Remove SQL from LANGUAGE_CONFIGS (if exists)**

```bash
grep -n '"sql":' packages/core/src/repowise/core/ingestion/parser.py
```

If found at line N, delete those lines

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(sql): remove tree-sitter-sql artifacts (query file, config entries)"
```

---

## Task 10: Install sqlglot and run tests

**Files:**
- Test: Install dependency and verify all tests pass

- [ ] **Step 1: Install sqlglot dependency**

```bash
cd packages/core && python3 -m pip install 'sqlglot>=30.0,<32' --break-system-packages
```

Expected: Package installs successfully

- [ ] **Step 2: Run SQL integration tests**

```bash
cd packages/core && python3 -m pytest tests/integration/test_sql_symbol_extraction.py -v
```

Expected: All 5 tests PASS

- [ ] **Step 3: Test bracket stripping specifically**

```bash
cd packages/core && python3 -c "
from tests.integration.test_sql_symbol_extraction import test_sql_bracket_stripping
test_sql_bracket_stripping()
print('✅ Bracket stripping test passed')
" 2>&1 | grep -v "UserWarning"
```

Expected: ✅ Bracket stripping test passed

- [ ] **Step 4: Test full fixture extraction**

```bash
cd packages/core && python3 -c "
from tests.integration.test_sql_symbol_extraction import test_sql_full_fixture
test_sql_full_fixture()
print('✅ Full fixture test passed')
" 2>&1 | grep -v "UserWarning"
```

Expected: ✅ Full fixture test passed

- [ ] **Step 5: Verify no regressions in other languages**

```bash
cd packages/core && python3 -m pytest tests/integration/test_symbol_extraction.py -v -k "python or typescript" --tb=short
```

Expected: Existing tests still PASS

- [ ] **Step 6: Final verification commit**

```bash
git add -A
git commit -m "test(sql): verify end-to-end sqlglot implementation - all tests passing"
```

---

## Task 11: Push branch for review

**Files:**
- Git: Push feature branch to remote

- [ ] **Step 1: Push branch to remote**

```bash
git push -u origin feat/sql-v2-sqlglot-instead-sqltree
```

Expected: Branch pushed successfully

- [ ] **Step 2: Create pull request (if desired)**

```bash
gh pr create --title "feat(sql): sqlglot-based SQL symbol extraction (100% PR1)" --body "$(cat <<'EOF'
## Summary
Replaces tree-sitter-sql with sqlglot parser to achieve 100% PR1 requirements.

### What's Included
- ✅ sqlglot>=30.0,<32 dependency
- ✅ SQL special handler using sqlglot T-SQL dialect
- ✅ Extract all 6 symbol types: TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX
- ✅ Bracket/backtick stripping: [dbo].[Users] → dbo.Users
- ✅ Schema defaulting: Users → dbo.Users (T-SQL)
- ✅ SQL promoted from Config/Data to Good tier

### Testing
- Integration tests: tests/integration/test_sql_symbol_extraction.py (5 tests)
- Fixture coverage: tests/fixtures/sql/schema.sql (all 6 symbol types)
- Zero parse errors for supported T-SQL syntax

### Architecture
- Special handler pattern (like openapi.py)
- Clean separation: sqlglot for SQL, tree-sitter for other languages
- AST-first extraction with regex fallback for complex statements

Closes #[issue-number]
EOF
)"
```

---

## Success Criteria Verification

After completing all tasks, verify:

- [ ] All 6 symbol types extract correctly (TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX)
- [ ] Bracket stripping works: `[dbo].[Users]` → `dbo.Users`
- [ ] Schema defaulting works: `Users` → `dbo.Users`
- [ ] Zero parse errors for supported T-SQL syntax
- [ ] All 5 integration tests pass
- [ ] No regressions in existing language support
- [ ] SQL moved to Good tier in documentation
- [ ] Branch pushed and ready for review

---

**Implementation estimated time:** 2-3 hours
**Testing estimated time:** 30 minutes
**Total time:** 3 hours
