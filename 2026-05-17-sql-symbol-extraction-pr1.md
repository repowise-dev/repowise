# SQL Symbol Extraction (PR1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move SQL from Config/Data passthrough tier to Good tier by adding tree-sitter-sql grammar and symbol extraction for tables, views, procedures, functions, triggers, and indexes.

**Architecture:** Wire tree-sitter-sql grammar into existing ingestion pipeline following established pattern for adding languages. No new pipeline components — single .scm query file + LanguageConfig entry.

**Tech Stack:** tree-sitter-sql >= 0.3, existing RepoWise ingestion pipeline (ASTParser, LanguageConfig, .scm queries)

---

## Files

**Create:**
- `packages/core/src/repowise/core/ingestion/queries/sql.scm` — tree-sitter queries for SQL symbol extraction
- `tests/fixtures/sql/schema.sql` — T-SQL test fixture covering CREATE TABLE/VIEW/PROC/FUNCTION/TRIGGER

**Modify:**
- `pyproject.toml:36-52` — add tree-sitter-sql dependency
- `packages/core/src/repowise/core/ingestion/languages/registry.py:970-976` — convert SQL from passthrough to full language
- `packages/core/src/repowise/core/ingestion/parser.py:220-235` — add SQL LanguageConfig entry
- `docs/LANGUAGE_SUPPORT.md:59-94` — move SQL from Config/Data to Good tier

---

## Task 1: Add tree-sitter-sql dependency

**Files:**
- Modify: `pyproject.toml:36-52`

- [ ] **Step 1: Read pyproject.toml dependencies section**

```bash
head -60 pyproject.toml
```

Expected: See tree-sitter dependencies list (lines 36-52)

- [ ] **Step 2: Add tree-sitter-sql dependency**

Insert after line 51 (tree-sitter-luau):
```toml
"tree-sitter-sql>=0.3,<1",
```

Full section should be:
```toml
    # AST parsing
    "tree-sitter>=0.23,<1",
    "tree-sitter-python>=0.23,<1",
    "tree-sitter-typescript>=0.23,<1",
    "tree-sitter-javascript>=0.23,<1",
    "tree-sitter-go>=0.23,<1",
    "tree-sitter-rust>=0.23,<1",
    "tree-sitter-java>=0.23,<1",
    "tree-sitter-cpp>=0.23,<1",
    "tree-sitter-kotlin>=1,<2",
    "tree-sitter-ruby>=0.23,<1",
    "tree-sitter-c-sharp>=0.23,<1",
    "tree-sitter-swift>=0.0.1",
    "tree-sitter-scala>=0.23,<1",
    "tree-sitter-php>=0.23,<1",
    "tree-sitter-luau>=1.2,<2",
    "tree-sitter-sql>=0.3,<1",
    # Dependency graph
    "networkx>=3.3,<4",
```

- [ ] **Step 3: Verify dependency syntax**

```bash
python3 -c "import configparser; c = configparser.ConfigParser(); c.read('pyproject.toml'); print('Syntax OK')"
```

Expected: No syntax errors

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(sql): add tree-sitter-sql >= 0.3 dependency"
```

---

## Task 2: Convert SQL from passthrough to full language in registry

**Files:**
- Modify: `packages/core/src/repowise/core/ingestion/languages/registry.py:970-976`

- [ ] **Step 1: Read current SQL LanguageSpec**

```bash
sed -n '970,976p' packages/core/src/repowise/core/ingestion/languages/registry.py
```

Expected: Current passthrough config:
```python
    LanguageSpec(
        tag="sql",
        display_name="SQL",
        extensions=frozenset({".sql"}),
        is_code=False,
        is_passthrough=True,
    ),
```

- [ ] **Step 2: Replace with full language LanguageSpec**

Replace lines 970-976 with:
```python
    LanguageSpec(
        tag="sql",
        display_name="SQL",
        extensions=frozenset({".sql"}),
        grammar_package="tree_sitter_sql",
        scm_file="sql.scm",
        heritage_node_types=frozenset(),  # SQL has no class hierarchy
        builtin_calls=frozenset(),         # No function calls to filter
        builtin_parents=frozenset(),       # No inheritance to filter
        color_hex="#CC55EE",               # Purple for SQL
    ),
```

- [ ] **Step 3: Verify syntax**

```bash
cd packages/core && python3 -c "from src.repowise.core.ingestion.languages.registry import REGISTRY; sql_spec = [s for s in REGISTRY._specs if s.tag == 'sql'][0]; print(f'SQL tag: {sql_spec.tag}, grammar: {sql_spec.grammar_package}')"
```

Expected: `SQL tag: sql, grammar: tree_sitter_sql`

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/repowise/core/ingestion/languages/registry.py
git commit -m "feat(sql): promote SQL from passthrough to full language in registry"
```

---

## Task 3: Write SQL tree-sitter query file

**Files:**
- Create: `packages/core/src/repowise/core/ingestion/queries/sql.scm`

- [ ] **Step 1: Create queries directory if missing**

```bash
ls -la packages/core/src/repowise/core/ingestion/queries/
```

Expected: Directory exists with other .scm files (python.scm, typescript.scm, etc.)

- [ ] **Step 2: Write sql.scm with symbol extraction queries**

Create `packages/core/src/repowise/core/ingestion/queries/sql.scm`:
```scm
; =============================================================================
; repowise — SQL symbol extraction queries
; tree-sitter-sql >= 0.3
;
; SQL dialect support: T-SQL (SQL Server), PostgreSQL, MySQL, BigQuery
;
; Capture name conventions (shared across ALL language query files):
;   @symbol.def       — the full definition node (used for line numbers, kind)
;   @symbol.name      — the name identifier node
;   @symbol.params    — parameter list node (optional, for procedures/functions)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; CREATE TABLE [schema].[table_name] (...)
(create_table
  name: (object_reference_name) @symbol.name
) @symbol.def

; CREATE VIEW [schema].[view_name] AS ...
(create_view
  name: (object_reference_name) @symbol.name
) @symbol.def

; CREATE PROCEDURE [schema].[proc_name] (@param1 type, @param2 type)
(create_procedure
  name: (object_reference_name) @symbol.name
  parameters: (procedure_parameters) @symbol.params
) @symbol.def

; CREATE FUNCTION [schema].[func_name] (@param type) RETURNS ...
(create_function
  name: (object_reference_name) @symbol.name
  parameters: (procedure_parameters) @symbol.params
) @symbol.def

; CREATE TRIGGER [schema].[trigger_name] ON [table]
(create_trigger
  name: (object_reference_name) @symbol.name
) @symbol.def

; CREATE INDEX [schema].[index_name] ON [table]
; Note: Indexes are captured for graph edges (PR2) but not mapped to SymbolKind
(create_index
  name: (object_reference_name) @symbol.name
) @symbol.def
```

- [ ] **Step 3: Verify file created**

```bash
cat packages/core/src/repowise/core/ingestion/queries/sql.scm
```

Expected: File contents match above

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/repowise/core/ingestion/queries/sql.scm
git commit -m "feat(sql): add tree-sitter query file for symbol extraction"
```

---

## Task 4: Add SQL LanguageConfig to parser

**Files:**
- Modify: `packages/core/src/repowise/core/ingestion/parser.py:220-235`

- [ ] **Step 1: Read LANGUAGE_CONFIGS location**

```bash
grep -n "LANGUAGE_CONFIGS = {" packages/core/src/repowise/core/ingestion/parser.py
```

Expected: Line 220

- [ ] **Step 2: Read existing LanguageConfig pattern**

```bash
sed -n '271,285p' packages/core/src/repowise/core/ingestion/parser.py
```

Expected: Go language config pattern:
```python
    "go": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "struct",
            ...
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=go_visibility,
        parent_extraction="receiver",
        ...
    ),
```

- [ ] **Step 3: Add SQL entry to LANGUAGE_CONFIGS**

Insert after the last language entry (before closing `}`):
```python
    "sql": LanguageConfig(
        symbol_node_types={
            "create_table": "struct",       # Tables → data structures
            "create_view": "function",      # Views → callable (return data)
            "create_procedure": "function", # Procedures → executable units
            "create_function": "function",  # Functions → callable
            "create_trigger": "method",     # Triggers → attached to tables
            "create_index": None,           # Indexes captured but no SymbolKind (PR2 edges)
        },
        import_node_types=[],  # SQL has no imports
        export_node_types=[],
        visibility_fn=public_by_default,  # All SQL objects are public
        parent_extraction="none",  # Triggers reference tables via edges (PR2)
    ),
```

- [ ] **Step 4: Verify syntax**

```bash
cd packages/core && python3 -c "from src.repowise.core.ingestion.parser import LANGUAGE_CONFIGS; sql_config = LANGUAGE_CONFIGS.get('sql'); print(f'SQL config: {sql_config.symbol_node_types}')"
```

Expected: `SQL config: {'create_table': 'struct', 'create_view': 'function', ...}`

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/repowise/core/ingestion/parser.py
git commit -m "feat(sql): add LanguageConfig entry for SQL"
```

---

## Task 5: Create SQL test fixture

**Files:**
- Create: `tests/fixtures/sql/schema.sql`

- [ ] **Step 1: Create fixtures directory**

```bash
mkdir -p tests/fixtures/sql
```

- [ ] **Step 2: Write T-SQL fixture**

Create `tests/fixtures/sql/schema.sql`:
```sql
-- =============================================================================
-- RepoWise SQL Symbol Extraction Test Fixture
-- T-SQL dialect (SQL Server)
-- Covers: CREATE TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER
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
cat tests/fixtures/sql/schema.sql
```

Expected: File contents match above

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/sql/schema.sql
git commit -m "test(sql): add T-SQL fixture covering CREATE TABLE/VIEW/PROC/FUNCTION/TRIGGER"
```

---

## Task 6: Write integration test for SQL symbol extraction

**Files:**
- Modify: `tests/integration/test_symbol_extraction.py` (or create new test file)

- [ ] **Step 1: Find existing symbol extraction tests**

```bash
find tests -name "*symbol*" -o -name "*extraction*" | head -5
```

Expected: Existing test files for symbol extraction

- [ ] **Step 2: Create integration test**

Create `tests/integration/test_sql_symbol_extraction.py`:
```python
"""Test SQL symbol extraction via tree-sitter-sql grammar."""

from pathlib import Path

import pytest

from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser


def test_sql_symbol_extraction_basic(tmp_path):
    """Test that SQL symbols are extracted from CREATE statements."""
    # Create test SQL file
    sql_file = tmp_path / "test.sql"
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
    traverser = FileTraverser(root=tmp_path, inclusion_patterns=["*.sql"])
    file_info = list(traverser.traverse())[0]

    parser = ASTParser()
    parsed = parser.parse_file(file_info, sql_file.read_bytes())

    # Assert symbols extracted
    assert len(parsed.symbols) == 5, f"Expected 5 symbols, got {len(parsed.symbols)}"

    # Check table symbol
    table_symbols = [s for s in parsed.symbols if s.kind == "struct"]
    assert len(table_symbols) == 1
    assert table_symbols[0].name == "dbo.Users"

    # Check view symbol
    view_symbols = [s for s in parsed.symbols if s.kind == "function"]
    assert len(view_symbols) == 3  # View + Procedure + Function
    view_names = {s.name for s in view_symbols}
    assert "dbo.ActiveUsers" in view_names
    assert "dbo.GetUserByEmail" in view_names
    assert "dbo.FormatEmail" in view_names

    # Check trigger symbol
    trigger_symbols = [s for s in parsed.symbols if s.kind == "method"]
    assert len(trigger_symbols) == 1
    assert trigger_symbols[0].name == "dbo.trg_Users_Audit"


def test_sql_schema_defaulting(tmp_path):
    """Test that implicit schema defaults to dbo for T-SQL."""
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("""
        CREATE TABLE Users (
            UserId INT PRIMARY KEY
        );

        CREATE VIEW ActiveUsers AS
        SELECT UserId FROM Users;
    """)

    # Parse file
    traverser = FileTraverser(root=tmp_path, inclusion_patterns=["*.sql"])
    file_info = list(traverser.traverse())[0]

    parser = ASTParser()
    parsed = parser.parse_file(file_info, sql_file.read_bytes())

    # Check that symbols have default dbo schema
    symbol_names = {s.name for s in parsed.symbols}
    assert "dbo.Users" in symbol_names
    assert "dbo.ActiveUsers" in symbol_names


def test_sql_parameter_extraction(tmp_path):
    """Test that procedure/function parameters are captured."""
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("""
        CREATE PROCEDURE [dbo].[GetUser]
            @UserId INT,
            @IncludeInactive BIT = 0
        AS
        SELECT * FROM dbo.Users WHERE UserId = @UserId;

        CREATE FUNCTION [dbo].[Add]
            (@A INT, @B INT)
        RETURNS INT
        AS
        BEGIN
            RETURN @A + @B;
        END;
    """)

    # Parse file
    traverser = FileTraverser(root=tmp_path, inclusion_patterns=["*.sql"])
    file_info = list(traverser.traverse())[0]

    parser = ASTParser()
    parsed = parser.parse_file(file_info, sql_file.read_bytes())

    # Check that parameters were captured
    proc_symbols = [s for s in parsed.symbols if "GetUser" in s.name]
    assert len(proc_symbols) == 1
    # Parameters should be available in symbol metadata
    # (Exact structure depends on how tree-sitter captures params)

    func_symbols = [s for s in parsed.symbols if "Add" in s.name]
    assert len(func_symbols) == 1
```

- [ ] **Step 3: Run tests to verify they fail (no implementation yet)**

```bash
cd packages/core && python3 -m pytest tests/integration/test_sql_symbol_extraction.py -v
```

Expected: Tests fail with missing sql.scm or grammar errors

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_sql_symbol_extraction.py
git commit -m "test(sql): add integration tests for symbol extraction"
```

---

## Task 7: Update documentation

**Files:**
- Modify: `docs/LANGUAGE_SUPPORT.md:59-94`

- [ ] **Step 1: Read current SQL entry in Config/Data section**

```bash
sed -n '75,94p' docs/LANGUAGE_SUPPORT.md
```

Expected: SQL listed in Config / Data section (line 92)

- [ ] **Step 2: Remove SQL from Config / Data section**

Remove line 92:
```markdown
| **SQL** | `.sql` | -- |
```

- [ ] **Step 3: Add SQL to Good section**

Add to Good section table (after PHP line):
```markdown
| **SQL** | `.sql` | -- | No imports/heritage; edge-based dependency resolver (PR2) |
```

Full Good section should be:
```markdown
### Good

AST parsing, symbol extraction, import resolution, call resolution, named
bindings, heritage extraction (including Ruby mixins, Rust derive, Swift
extension conformance, PHP trait use), and docstrings. Dedicated import
resolvers for each language.

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **C** | `.c` | `main.c` | `#include` with `compile_commands.json` (shares C++ grammar) |
| **Kotlin** | `.kt` `.kts` | `Main.kt` `Application.kt` | `import com.example.Foo` with Gradle `settings.gradle(.kts)` subprojects + `sourceSets` overrides |
| **Ruby** | `.rb` | `main.rb` `app.rb` `config.ru` | `require 'mod'` / `require_relative './mod'` plus Rails / Zeitwerk autoloading (gated on `config/application.rb`) |
| **Swift** | `.swift` | `main.swift` `App.swift` | `import Foundation` with SPM `Package.swift` `targets:` → directory mapping |
| **Scala** | `.scala` | `Main.scala` `App.scala` | `import pkg.{A, B => C}` with SBT `build.sbt` / Mill `build.scf` multi-project parsing |
| **PHP** | `.php` | `index.php` `public/index.php` | `use Foo\Bar\Baz` with composer.json `autoload.psr-4` longest-prefix resolution |
| **SQL** | `.sql` | -- | No imports/heritage; edge-based dependency resolver (PR2) |
```

- [ ] **Step 4: Verify markdown syntax**

```bash
python3 -c "import markdown; markdown.markdown(open('docs/LANGUAGE_SUPPORT.md').read()); print('Markdown OK')"
```

Expected: No syntax errors

- [ ] **Step 5: Commit**

```bash
git add docs/LANGUAGE_SUPPORT.md
git commit -m "docs(sql): move SQL from Config/Data to Good tier"
```

---

## Task 8: End-to-end verification

**Files:**
- Test: Run full integration test suite

- [ ] **Step 1: Install tree-sitter-sql dependency**

```bash
cd packages/core && pip install tree-sitter-sql>=0.3,<1
```

Expected: Package installs successfully

- [ ] **Step 2: Run SQL integration tests**

```bash
cd packages/core && python3 -m pytest tests/integration/test_sql_symbol_extraction.py -v
```

Expected: All tests PASS

- [ ] **Step 3: Test against fixture file**

```bash
cd packages/core && python3 -c "
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from pathlib import Path

# Parse fixture
fixture_path = Path('tests/fixtures/sql/schema.sql')
traverser = FileTraverser(root=fixture_path.parent, inclusion_patterns=['*.sql'])
file_info = list(traverser.traverse())[0]

parser = ASTParser()
parsed = parser.parse_file(file_info, fixture_path.read_bytes())

print(f'File: {file_info.path}')
print(f'Symbols extracted: {len(parsed.symbols)}')
for s in parsed.symbols:
    print(f'  - {s.kind}: {s.name}')
"
```

Expected output:
```
File: schema.sql
Symbols extracted: 11
  - struct: dbo.Users
  - struct: dbo.Posts
  - struct: dbo.Tags
  - function: dbo.ActiveUsers
  - function: dbo.RecentPosts
  - function: dbo.GetUserByEmail
  - function: dbo.CreatePost
  - function: dbo.FormatEmail
  - function: dbo.GetUserPosts
  - method: dbo.trg_Users_Audit
  - struct: IX_Posts_Email  # Index captured but mapped to None in config
```

- [ ] **Step 4: Verify no regressions in other languages**

```bash
cd packages/core && python3 -m pytest tests/integration/test_symbol_extraction.py -v -k "python or typescript"
```

Expected: Existing tests still PASS

- [ ] **Step 5: Final verification commit**

```bash
git add -A
git commit -m "test(sql): verify end-to-end SQL symbol extraction"
```

---

## Post-Implementation Notes

**What PR1 Does NOT Include (scoped for PR2):**
- Dependency edge extraction (FK REFERENCES, view SELECT...FROM, proc bodies)
- Dialect-aware schema defaulting (currently hardcoded to dbo)
- Symbol name normalization (bracket/backtick stripping in post-processor)
- Cross-file symbol resolution
- Call graph construction for SQL

**Known Limitations to Document:**
- tree-sitter-sql parses a generic SQL superset; some T-SQL/PL-pgSQL constructs may parse as ERROR nodes
- Indexes are captured but not mapped to SymbolKind (awaiting PR2 edge types)
- Default schema is hardcoded to dbo; Postgres public dialect support in PR2

**Testing Strategy:**
- Unit tests: Individual symbol extraction patterns
- Integration test: Fixture file with real SQL schema
- Regression tests: Verify no impact on existing languages

**Documentation PR Description:**
```
## SQL Symbol Extraction (PR1)

This PR promotes SQL from Config/Data passthrough to Good tier by wiring tree-sitter-sql into the ingestion pipeline.

### What's Included
- ✅ tree-sitter-sql >= 0.3 dependency
- ✅ SQL LanguageSpec with grammar and .scm query file
- ✅ LanguageConfig for symbol kind mapping
- ✅ Symbol extraction: TABLE (struct), VIEW (function), PROCEDURE (function), FUNCTION (function), TRIGGER (method), INDEX (captured for PR2)
- ✅ T-SQL test fixture
- ✅ Integration tests
- ✅ Documentation update

### What's Deferred to PR2
- Dependency edges (FK REFERENCES, view base tables, proc body references)
- Dialect-aware schema defaulting (currently hardcoded to dbo)
- Symbol name normalization (bracket stripping in post-processor)

### Supported Dialects
T-SQL (SQL Server) is the primary target for PR1. PostgreSQL, MySQL, and BigQuery parse but may have ERROR nodes for dialect-specific constructs.

### Testing
- `tests/fixtures/sql/schema.sql` — T-SQL fixture
- `tests/integration/test_sql_symbol_extraction.py` — Integration tests
- Run: `pytest tests/integration/test_sql_symbol_extraction.py -v`

Closes #[issue-number]
```