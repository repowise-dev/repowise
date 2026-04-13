# Phase 3 Handoff — Complete & Add Languages

**For:** Next Claude Code session  
**Date:** 2026-04-13  
**Branch:** `feat/language-support`  
**Status:** Code restructuring DONE (Phase 1 of LANGUAGE_SUPPORT_PLAN.md). Language completions ready to start.

---

## What's Done

Two commits on `feat/language-support`:

### Commit 1: `9b0a1f5` — LanguageRegistry (Phase 1)

Created a centralised `LanguageRegistry` with 42 `LanguageSpec` entries.
Migrated 14 consumer files. Deleted stale `packages/core/queries/`.

### Commit 2: `65390d8` — Modularize extractors & resolvers

Extracted per-language logic from the two biggest hotspot files:

| File | Before | After |
|------|--------|-------|
| `parser.py` | 1,806 lines | 796 lines |
| `graph.py` | 1,286 lines | 646 lines |

Created:
- `extractors/` — 7 files: visibility, signatures, docstrings, bindings, heritage, helpers, __init__
- `resolvers/` — 8 files: context, python, typescript, go, rust, cpp, generic, __init__
- `framework_edges.py` — Django, FastAPI, Flask, pytest conftest
- Deleted dead `parsers/` stubs

### Test results
- 1093 passed, 0 failed (1 pre-existing failure in `test_mcp_full_exploration_flow` — `KeyError: 'architecture_diagram_mermaid'`, unrelated)

---

## What's NOT Done — The Actual Language Work

The code restructuring (Phase 1 of `LANGUAGE_SUPPORT_PLAN.md`) is complete.
The **language completion work** (Phase 2 of that plan) and **new language
work** (Phase 3 of that plan) are untouched. Here's exactly what remains:

### Tier 1: Harden existing languages

#### C++ → Full tier (~150 LOC)

C++ already has: grammar, `.scm` with `@call.*` captures, heritage
extractor, compile_commands.json resolver.

**Missing:**
- Named binding extractor for `#include` — add `extract_cpp_bindings()` in
  `extractors/bindings.py` (extract header filename as binding)
- Docstring extraction — add C++/Doxygen support (`/** ... */` at file top
  + before declarations) in `extractors/docstrings.py`
- Add `"cpp"` visibility function if needed (currently uses `public_by_default`)

#### C → Partial+ tier (~100 LOC)

C shares the C++ grammar via `shares_grammar_with="cpp"`.

**Missing:**
- Add `@call.*` captures to `c.scm` (currently only has symbols + imports,
  no call graph). Copy patterns from `cpp.scm`, narrow to C function calls.
- Named binding extractor (same as C++ — `#include` filename extraction)
- Doxygen docstring support (shared with C++)

### Tier 2: Wire up scaffolded languages

#### Kotlin → Good tier (~350 LOC)

Kotlin has: `.scm` file (symbols + imports), heritage extractor
(`_extract_kotlin_heritage` in `extractors/heritage.py`), builtin data in
registry.

**Missing:**
1. Add `tree-sitter-kotlin` to `pyproject.toml` dependencies
2. Add `LanguageConfig` entry in `parser.py` (symbol_node_types,
   import_node_types, visibility_fn, parent_extraction, etc.)
3. Add `@call.*` captures to `kotlin.scm`
4. Write `extract_kotlin_bindings()` in `extractors/bindings.py` for
   `import_header` nodes
5. Add Kotlin visibility function in `extractors/visibility.py` (Kotlin has
   `public`/`private`/`protected`/`internal`)
6. Write import resolver in `resolvers/kotlin.py` (package-based, similar
   to Java) and register in `resolvers/__init__.py`
7. Add KDoc docstring extraction (`/** ... */`) in `extractors/docstrings.py`

#### Ruby → Good tier (~350 LOC)

Ruby has: `.scm` file (symbols + `require` imports), heritage extractor
(`_extract_ruby_heritage` in `extractors/heritage.py`), builtin data.

**Missing:**
1. Add `tree-sitter-ruby` to `pyproject.toml`
2. Add `LanguageConfig` entry in `parser.py`
3. Add `@call.*` captures to `ruby.scm`
4. Write `extract_ruby_bindings()` in `extractors/bindings.py` for
   `require`/`require_relative`
5. Write import resolver in `resolvers/ruby.py` (`require` → file path,
   `require_relative` → relative) and register
6. Add Ruby docstring extraction (YARD `# @param` / RDoc) in
   `extractors/docstrings.py`

#### C# → Good tier (~500 LOC)

C# has: heritage extractor (`_extract_csharp_heritage` in
`extractors/heritage.py`), builtin data. Nothing else.

**Missing:**
1. Add `tree-sitter-c-sharp` to `pyproject.toml`
2. Write `csharp.scm` query file from scratch (classes, interfaces,
   methods, enums, properties, `using` statements, method invocations)
3. Add `LanguageConfig` entry in `parser.py`
4. Write `extract_csharp_bindings()` in `extractors/bindings.py` for
   `using` directives
5. Add C# visibility function in `extractors/visibility.py`
6. Write import resolver in `resolvers/csharp.py` (namespace-based,
   similar to Java but with `using` aliasing) and register
7. Add C# XML doc comment extraction (`/// <summary>`) in
   `extractors/docstrings.py`

### Tier 3: New languages from scratch

#### Swift → Good tier (~500 LOC)

**Everything needed:**
1. `tree-sitter-swift` dependency
2. Write `swift.scm` (functions, classes, structs, protocols, enums,
   extensions, imports, call expressions)
3. `LanguageConfig` entry
4. Binding extractor (Swift imports are module-level, no named imports)
5. Import resolver (Swift Package Manager — `Package.swift`, module-level)
6. Heritage extractor: `class Foo: Bar, Protocol1`
7. Docstring: `///` and `/** ... */` Swift doc comments
8. Builtins: Foundation framework calls + `NSObject`, `Codable`, etc.

#### Scala → Good tier (~550 LOC)

1. `tree-sitter-scala` dependency
2. Write `scala.scm` (functions, classes, traits, objects, vals, imports,
   call expressions)
3. `LanguageConfig` entry
4. Binding extractor: `import pkg.{A, B => C}`, `import pkg._`
5. Import resolver (SBT — `build.sbt`, package-based)
6. Heritage: `class Foo extends Bar with Trait1 with Trait2`
7. Docstring: ScalaDoc `/** ... */`
8. Builtins: `scala.*`, Predef, `Any`, `AnyRef`, `Product`, `Serializable`

#### PHP → Good tier (~550 LOC)

1. `tree-sitter-php` dependency
2. Write `php.scm` (functions, classes, interfaces, traits, methods,
   namespace use declarations, function calls)
3. `LanguageConfig` entry
4. Binding extractor: `use Foo\Bar\Baz`, `use Foo\Bar as B`
5. Import resolver (Composer — `composer.json` PSR-4 mappings, namespace →
   directory)
6. Heritage: `class Foo extends Bar implements Interface1`, `use TraitName`
7. Docstring: PHPDoc `/** ... */`
8. Builtins: PHP globals + `stdClass`, `Exception`, `Iterator`

#### Dart, Elixir (stretch goals)

See `docs/LANGUAGE_SUPPORT_PLAN.md` sections 3.4 and 3.5 for details.

---

## Where to Add Things — Quick Reference

| What | Where |
|------|-------|
| Language identity data | `ingestion/languages/registry.py` — add `LanguageSpec` to `_SPECS` |
| Language tag type | `ingestion/models.py` — add to `LanguageTag` Literal |
| Tree-sitter query | `ingestion/queries/<lang>.scm` |
| Parser config | `ingestion/parser.py` — add to `LANGUAGE_CONFIGS` dict |
| Visibility function | `ingestion/extractors/visibility.py` — add function + register in `VISIBILITY_FNS` |
| Binding extractor | `ingestion/extractors/bindings.py` — add function + register in `extract_import_bindings()` |
| Heritage extractor | `ingestion/extractors/heritage.py` — add function + register in `HERITAGE_EXTRACTORS` |
| Docstring extraction | `ingestion/extractors/docstrings.py` — add elif branch |
| Signature building | `ingestion/extractors/signatures.py` — add elif branch if needed |
| Import resolver | `ingestion/resolvers/<lang>.py` — create file + register in `resolvers/__init__.py` `_RESOLVERS` |
| Grammar dependency | `pyproject.toml` under `[project] dependencies` |

All paths relative to `packages/core/src/repowise/core/`.

---

## Recommended Execution Order

```
1. C++ bindings + docstrings        (~150 LOC, smallest, validates patterns)
2. C call captures + bindings       (~100 LOC, same pattern)
3. Kotlin full wiring               (~350 LOC, first "new grammar" activation)
4. Ruby full wiring                 (~350 LOC, same pattern as Kotlin)
5. C# from scratch                  (~500 LOC, heaviest existing, validates full workflow)
6. Swift                            (~500 LOC, independent)
7. Scala                            (~550 LOC, independent)
8. PHP                              (~550 LOC, independent)
```

Items 1-5 harden existing languages. Items 6-8 are new languages, fully
independent of each other.

---

## Testing Strategy

For each language:
1. Add sample files to `tests/fixtures/sample-repo/` in that language
2. Add integration tests asserting: symbols extracted, imports resolved,
   call edges created, heritage edges created
3. Add unit tests for the binding extractor and resolver
4. Run full suite: `pytest tests/ --deselect tests/integration/test_mcp.py::test_mcp_full_exploration_flow`
5. Expected baseline: 1093 passed, 0 regressions

---

## Key Architectural Constraints

1. **Registry is a leaf dependency** — `languages/registry.py` imports
   nothing from ingestion. Extractors and resolvers import from it, not
   the other way around.

2. **Extractors depend on tree-sitter** — they import `tree_sitter.Node`.
   They must NOT be imported by the registry.

3. **Resolvers depend on networkx** (via `ResolverContext.graph`) — they
   must NOT be imported by the registry or extractors.

4. **parser.py is orchestration only** — no per-language if/elif chains.
   All language-specific logic dispatches through `LANGUAGE_CONFIGS`,
   `extractors/`, and `.scm` query files.

5. **graph.py is orchestration only** — import resolution dispatches
   through `resolvers/`. Framework edges dispatch through
   `framework_edges.py`.

6. **Pre-existing test failure**: `test_mcp_full_exploration_flow` fails
   with `KeyError: 'architecture_diagram_mermaid'` — unrelated to
   language support work.

---

## Reference Files

| File | Purpose |
|------|---------|
| `docs/LANGUAGE_SUPPORT_PLAN.md` | Full 3-phase plan (Phase 1 done, Phase 2/3 pending) |
| `docs/LANGUAGE_SUPPORT.md` | User-facing language support doc |
| `ingestion/languages/registry.py` | LanguageRegistry (42 specs) |
| `ingestion/languages/spec.py` | LanguageSpec dataclass |
| `ingestion/extractors/` | Per-language extraction (7 files) |
| `ingestion/resolvers/` | Per-language import resolution (8 files) |
| `ingestion/framework_edges.py` | Framework edge detection |
| `ingestion/parser.py` | ASTParser + LANGUAGE_CONFIGS (796 lines) |
| `ingestion/graph.py` | GraphBuilder (646 lines) |
| `ingestion/queries/` | Tree-sitter .scm query files |
