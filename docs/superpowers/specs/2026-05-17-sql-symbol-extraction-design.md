# SQL Symbol Extraction Design

**Date:** 2026-05-17
**Status:** Design Phase
**Goal:** Replace tree-sitter-sql with sqlglot to meet 100% of PR1 requirements

## Problem Statement

Current implementation using tree-sitter-sql 0.3.x fails PR1 acceptance criteria:

- ❌ CREATE PROCEDURE: Not supported (65 parse errors)
- ❌ CREATE FUNCTION: Parses with errors, symbols not extracted
- ❌ CREATE TRIGGER: Not supported
- ❌ Bracket stripping: `[dbo].[Users]` → `dbo].[Users` (artifacts remain)
- ❌ Schema defaulting: Not implemented

**Completion:** ~33% of PR1 requirements (only TABLE + VIEW work)

## Solution: sqlglot Parser

**sqlglot** is a mature SQL parser/transpiler with native T-SQL dialect support that successfully extracts all 6 PR1-required symbol types:

| Symbol Type | tree-sitter-sql | sqlglot |
|-------------|-----------------|---------|
| CREATE TABLE | ✅ Works | ✅ Works |
| CREATE VIEW | ✅ Works | ✅ Works |
| CREATE PROCEDURE | ❌ ERROR nodes | ✅ Works |
| CREATE FUNCTION | ❌ Partial/broken | ✅ Works |
| CREATE TRIGGER | ❌ ERROR nodes | ✅ Works |
| CREATE INDEX | ⚠️ Limited | ✅ Works |

**Test Results:**
```
✅ TABLE:    "dbo.Users"
✅ VIEW:     "dbo.ActiveUsers"
✅ PROCEDURE: "dbo.spTest"
✅ FUNCTION:  "dbo.fnTest"
✅ FUNCTION:  "dbo.Add" (with parameters)
✅ TRIGGER:  "dbo.trTest"
✅ INDEX:    "IX_Users_Email"
```

## Architecture Decisions

### 1. Dependency Strategy: Required Dependency

**Decision:** Add `sqlglot>=30.0,<32` as required dependency

**Rationale:**
- Predictable user experience - no optional install steps
- sqlglot is mature (v30.8.0, active development)
- Consistent with other required dependencies (anthropic, openai, etc.)
- 30MB size acceptable for functionality gained

**Trade-off:** Larger dependency size vs. fragmented optional dependencies

### 2. Integration Pattern: Special Handler

**Decision:** Add `packages/core/src/repowise/core/ingestion/special_handlers/sql.py`

**Rationale:**
- Follows established pattern (openapi.py, dockerfile.py, makefile.py)
- Clean separation: tree-sitter for languages, sqlglot for SQL
- Easy to test, maintain, and swap implementations
- SQL parsing is fundamentally different (no tree-sitter grammar needed)

**Architecture:**
```
ASTParser.parse_file()
├── lang == "sql" → special_handlers.parse_sql_file()
│   └── Uses sqlglot for parsing
└── lang != "sql" → tree-sitter parsing (existing)
```

### 3. Error Handling: Consistent Capture

**Decision:** Capture sqlglot warnings/errors as `parse_errors` in ParsedFile

**Rationale:**
- Consistent UX across all parsers
- Users see SQL issues in their repo analysis
- Enables debugging of dialect-specific issues

## Component Design

### Special Handler: `sql.py`

**Location:** `packages/core/src/repowise/core/ingestion/special_handlers/sql.py`

**Responsibilities:**
1. Parse SQL source using sqlglot T-SQL dialect
2. Extract symbols from CREATE statements (TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX)
3. Strip bracket/backtick quoting: `[dbo].[Users]` → `dbo.Users`
4. Default schema to `dbo` (T-SQL) when implicit
5. Map SQL kinds to RepoWise SymbolKinds
6. Return ParsedFile with symbols and parse_errors

**Interface:**
```python
def parse_sql_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Parse SQL file using sqlglot, extract symbols.

    Args:
        file_info: File metadata
        source: SQL source code bytes

    Returns:
        ParsedFile with extracted symbols
    """
```

### Symbol Extraction Logic

**Approach: AST-first with regex fallback**

```python
def extract_symbols(ast, source: str) -> list[Symbol]:
    """Extract symbols from sqlglot AST.

    Strategy:
    1. AST walking for clean parses (TABLE, PROCEDURE, INDEX)
    2. Regex fallback for complex statements (FUNCTION, TRIGGER, VIEW)
    3. Schema defaulting: implicit → dbo (T-SQL)
    """
    symbols = []

    for statement in ast:
        # Try AST extraction
        if hasattr(statement, 'kind') and hasattr(statement, 'this'):
            kind = statement.kind
            if kind == "TABLE":
                name = extract_from_table_node(statement)
            elif kind == "PROCEDURE":
                name = extract_from_procedure_node(statement)
            elif kind == "INDEX":
                name = extract_from_index_node(statement)
            else:
                # Regex fallback for VIEW, FUNCTION, TRIGGER
                name = extract_from_regex(statement.sql)

            if name:
                symbols.append(Symbol(
                    name=strip_brackets(name),
                    kind=map_to_symbol_kind(kind),
                    ...
                ))

    return symbols
```

### Kind Mapping

| SQL Kind | RepoWise SymbolKind | Rationale |
|----------|-------------------|-----------|
| TABLE | `struct` | Data structure |
| VIEW | `function` | Callable (returns data) |
| PROCEDURE | `function` | Executable unit |
| FUNCTION | `function` | Callable |
| TRIGGER | `method` | Attached to table |
| INDEX | `None` | Captured but no SymbolKind (PR2 edges) |

### Bracket Stripping

**Logic:**
```python
def strip_brackets(name: str) -> str:
    """Strip SQL identifier quoting.

    T-SQL: [dbo].[Users] → dbo.Users
    MySQL: `dbo`.`Users` → dbo.Users
    PostgreSQL: "dbo"."Users" → dbo.Users
    """
    return name.replace('[', '').replace(']', '').replace('`', '').replace('"', '')
```

### Schema Defaulting

**Logic:**
```python
def default_schema(name: str, dialect: str = "tsql") -> str:
    """Default schema when implicit.

    T-SQL: Users → dbo.Users
    PostgreSQL: users → public.users
    """
    if '.' not in name:
        default = "dbo" if dialect == "tsql" else "public"
        return f"{default}.{name}"
    return name
```

## Implementation Plan

### Phase 1: Foundation
- [ ] Add `sqlglot>=30.0,<32` to pyproject.toml
- [ ] Create `special_handlers/sql.py` skeleton
- [ ] Update LanguageSpec in registry.py: `special_handler="sql"`
- [ ] Wire up special handler call in parser.py

### Phase 2: Core Extraction
- [ ] Implement `parse_sql_file()` function
- [ ] Implement AST-based extraction (TABLE, PROCEDURE, INDEX)
- [ ] Implement regex fallback (VIEW, FUNCTION, TRIGGER)
- [ ] Add bracket/quote stripping logic
- [ ] Add schema defaulting logic
- [ ] Implement SymbolKind mapping

### Phase 3: Registry Integration
- [ ] Update LanguageSpec to use special_handler instead of grammar
- [ ] Remove tree-sitter-sql from LANGUAGE_CONFIGS
- [ ] Update parser.py to route SQL → special handler

### Phase 4: Testing
- [ ] Create unit tests for extraction logic
- [ ] Create integration test with T-SQL fixture
- [ ] Test bracket stripping: `[dbo].[Users]` → `dbo.Users`
- [ ] Test schema defaulting: `Users` → `dbo.Users`
- [ ] Verify all 6 symbol types extract correctly
- [ ] Verify zero parse errors for supported syntax

### Phase 5: Cleanup
- [ ] Remove tree-sitter-sql dependency from pyproject.toml
- [ ] Delete `queries/sql.scm` file (no longer needed)
- [ ] Remove SQL from _PASSTHROUGH_LANGUAGES
- [ ] Update LANGUAGE_SUPPORT.md: remove "edge-based dependency resolver (PR2)" caveat
- [ ] Update tests to match new behavior

## Success Criteria

### Functional Requirements
- ✅ Extract all 6 PR1 symbol types: TABLE, VIEW, PROCEDURE, FUNCTION, TRIGGER, INDEX
- ✅ Bracket stripping works: `[dbo].[Users]` → `dbo.Users`
- ✅ Schema defaulting works: `Users` → `dbo.Users`
- ✅ Zero parse errors for supported T-SQL syntax
- ✅ SQL promoted from Config/Data → Good tier

### Quality Requirements
- ✅ Follows existing RepoWise patterns (special_handlers)
- ✅ Consistent with other parsers (parse_errors, FileInfo, etc.)
- ✅ Tests pass for all symbol types
- ✅ No regression in existing language support
- ✅ Documentation updated and accurate

### Performance Requirements
- ✅ Parsing speed comparable to tree-sitter for other languages
- ✅ No significant increase in memory usage
- ✅ Handles large SQL files (>1000 lines) efficiently

## Migration Path

### Breaking Changes
- **Removed:** `tree-sitter-sql` dependency
- **Removed:** `queries/sql.scm` file
- **Changed:** SQL now uses special_handler instead of tree-sitter grammar

### Compatibility
- ✅ Existing symbol extraction for other languages unchanged
- ✅ Existing tests for other languages unchanged
- ✅ API surface unchanged (still returns ParsedFile)

## Rollback Plan

If issues arise, rollback steps:
1. Revert commits adding sqlglot integration
2. Restore `queries/sql.scm` and tree-sitter-sql dependency
3. Revert LanguageSpec changes
4. SQL returns to partial support (TABLE + VIEW only)

## References

- **sqlglot GitHub:** https://github.com/tobymao/sqlglot
- **sqlglot T-SQL Docs:** https://sqlglot.com/sqlglot/dialects/tsql.html
- **sqlglot AST Primer:** https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md
- **Original PR1 Plan:** `2026-05-17-sql-symbol-extraction-pr1.md`

## Appendix: Test Cases

### Comprehensive Test Fixture

**Input SQL:**
```sql
CREATE TABLE [dbo].[Users](
    [UserId] INT PRIMARY KEY,
    [Email] NVARCHAR(256)
);

CREATE VIEW [dbo].[ActiveUsers]
AS
SELECT UserId FROM dbo.Users;

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
PRINT 'Users table modified';

CREATE INDEX [IX_Users_Email]
ON [dbo].[Users]([Email]);

-- Schemaless (implicit dbo)
CREATE TABLE Tags (
    TagId INT PRIMARY KEY
);
```

**Expected Output:**
```python
symbols = [
    Symbol(name="dbo.Users", kind="struct"),
    Symbol(name="dbo.ActiveUsers", kind="function"),
    Symbol(name="dbo.GetUserByEmail", kind="function"),
    Symbol(name="dbo.FormatEmail", kind="function"),
    Symbol(name="dbo.trg_Users_Audit", kind="method"),
    Symbol(name="dbo.Tags", kind="struct"),  # schema defaulted
    # INDEX captured but filtered (kind=None)
]
```

**Expected Parse Errors:** 0

---

**Status:** Ready for implementation planning
