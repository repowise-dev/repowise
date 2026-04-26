# Language Support

repowise analyses codebases written in many languages. Each language goes
through a multi-stage pipeline: file traversal, AST parsing, import
resolution, call graph construction, and heritage (inheritance) extraction.
Not every language has reached full coverage yet --- this page documents
exactly what works today, what is coming next, and how to add a new language.

---

## Support Tiers

Every language falls into one of five tiers. The tier determines which
pipeline stages produce meaningful output.

| Stage | Full | Partial | Scaffolded | Traversal | Config / Data |
|-------|:----:|:-------:|:----------:|:---------:|:-------------:|
| File discovery & git history | Y | Y | Y | Y | Y |
| AST symbol extraction | Y | Y | -- | -- | -- |
| Import resolution | Y | Y | -- | -- | -- |
| Call graph edges | Y | partial | -- | -- | -- |
| Heritage (extends/implements) | Y | partial | -- | -- | -- |
| Named bindings | Y | -- | -- | -- | -- |
| Dead code detection | Y | Y | Y | Y | -- |
| Semantic search & wiki pages | Y | Y | Y | Y | Y |

---

## Language Reference

### Full

Languages with complete pipeline coverage: AST parsing, import resolution,
call resolution, named bindings, heritage extraction, and docstrings.

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **Python** | `.py` `.pyi` | `main.py` `app.py` `__main__.py` `manage.py` `wsgi.py` `asgi.py` | `import x` / `from x import y` |
| **TypeScript** | `.ts` `.tsx` | `index.ts` `main.ts` `app.ts` `server.ts` | `import { x } from 'y'` / `require()` |
| **JavaScript** | `.js` `.jsx` `.mjs` `.cjs` | `index.js` `main.js` `app.js` `server.js` | `import` / `require()` |
| **Java** | `.java` | `Main.java` `Application.java` | `import pkg.Class` |
| **Go** | `.go` | `main.go` `cmd/main.go` | `import "path"` with `go.mod` resolution |
| **Rust** | `.rs` | `main.rs` `lib.rs` | `use crate::` / `use super::` / `use self::` with `Cargo.toml` |
| **C++** | `.cpp` `.cc` `.cxx` `.h` `.hpp` `.hxx` | `main.cpp` `main.cc` | `#include` with `compile_commands.json` resolution |

All seven languages support:
- Tree-sitter AST parsing with dedicated `.scm` query files
- Three-tier call resolution (same-file, cross-file, global stem match)
- Named binding extraction (mapping imported names to source symbols)
- Heritage extraction (class/interface/trait inheritance chains)
- Docstring extraction (Python, JSDoc, GoDoc, Rustdoc, Javadoc, Doxygen)
- Framework-aware edges (Django, FastAPI, Flask for Python; tsconfig path aliases for TS/JS; pytest fixture detection)

### Good

AST parsing, symbol extraction, import resolution, call resolution, named
bindings, heritage extraction (including Ruby mixins, Rust derive, Swift
extension conformance, PHP trait use), and docstrings. Dedicated import
resolvers for each language.

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **C** | `.c` | `main.c` | `#include` with `compile_commands.json` (shares C++ grammar) |
| **Kotlin** | `.kt` `.kts` | `Main.kt` `Application.kt` | `import com.example.Foo` |
| **Ruby** | `.rb` | `main.rb` `app.rb` `config.ru` | `require 'mod'` / `require_relative './mod'` |
| **C#** | `.cs` | `Program.cs` `Startup.cs` | `using System.Collections.Generic` |
| **Swift** | `.swift` | `main.swift` `App.swift` | `import Foundation` |
| **Scala** | `.scala` | `Main.scala` `App.scala` | `import pkg.{A, B => C}` |
| **PHP** | `.php` | `index.php` `public/index.php` | `use Foo\Bar\Baz` |

### Config / Data

Non-code files included in the file tree and wiki. Special handlers extract
endpoints or targets where applicable.

| Language | Extensions / Filenames | Special Handler |
|----------|----------------------|----------------|
| **OpenAPI** | YAML/JSON with `openapi` or `swagger` key | Extracts API paths and schemas |
| **Dockerfile** | `Dockerfile` | Extracts stages and exposed ports |
| **Makefile** | `Makefile` `GNUmakefile` | Extracts targets |
| **Protobuf** | `.proto` | -- |
| **GraphQL** | `.graphql` `.gql` | -- |
| **Terraform** | `.tf` `.hcl` | -- |
| **YAML** | `.yaml` `.yml` | -- |
| **JSON** | `.json` | -- |
| **TOML** | `.toml` | -- |
| **Markdown** | `.md` `.mdx` | -- |
| **SQL** | `.sql` | -- |
| **Shell** | `.sh` `.bash` `.zsh` | -- |

### Partial (Luau — Roblox)

| Language | Extensions | Entry Points | Import Style |
|----------|-----------|-------------|-------------|
| **Luau** | `.luau` `.lua` | `init.luau` `init.lua` | `require(script.Parent.X)` / `require(script.X)` / `require(game.Service.Path)` / `require("rel/path")` |

AST parsing, symbol extraction (functions, Luau type aliases), and
`require(...)` call capture are wired. Import resolution handles string
literals and `script`/`script.Parent` relative instance paths. Absolute
Roblox instance paths (`game.<Service>...`) currently register as external
nodes and are the target of a follow-up that reads Rojo's
`default.project.json` tree mapping — see issue #52.

### Git-Blame-Only

These languages are tracked in git history (blame, hotspot analysis,
co-change detection) but have no AST parsing or dedicated support. Files
appear in the wiki as traversal-level entries.

Objective-C, Elixir, Erlang, R, Dart, Zig, Julia, Clojure, Elm,
Haskell, OCaml, F#, Crystal, Nim, D

---

## How the Pipeline Processes a File

```
File discovered by FileTraverser
        |
        v
Extension/filename -> LanguageTag  (via LanguageRegistry)
        |
        +-- Config/data language?  -> empty ParsedFile (passthrough)
        +-- Special format?        -> special_handlers.py (OpenAPI/Dockerfile/Makefile)
        +-- Has grammar?           -> tree-sitter AST parsing
                |
                v
        .scm query extracts:
          @symbol.def / @symbol.name    -> Symbol nodes
          @import.statement / @import.module -> Import edges
          @call.target / @call.receiver     -> Call edges
                |
                v
        Per-language extractors:
          - Named bindings (import name -> source symbol)
          - Heritage (extends/implements/traits)
          - Docstrings (Python, JSDoc, GoDoc, Rustdoc, Javadoc)
          - Visibility (public/private/protected)
                |
                v
        GraphBuilder resolves imports:
          Python: dotted module paths, __init__.py, src/ layout
          TS/JS:  relative paths, tsconfig aliases, node_modules
          Go:     go.mod module path stripping
          Rust:   crate::/self::/super::, mod.rs probing
          C/C++:  compile_commands.json include directories
          Other:  stem-map fallback (filename matching)
                |
                v
        Graph analysis:
          PageRank, community detection, dead code, execution flows
```

---

## Adding a New Language

The pipeline is fully modular. Language identity data lives in the
centralised `LanguageRegistry`, per-language extraction logic lives in
`extractors/`, and per-language import resolution lives in `resolvers/`.
Adding a new language touches these places:

### Step 1: Add a `LanguageSpec` to the registry

Edit `packages/core/src/repowise/core/ingestion/languages/registry.py` and
add a new `LanguageSpec(...)` entry to the `_SPECS` tuple:

```python
LanguageSpec(
    tag="mylang",
    display_name="MyLang",
    extensions=frozenset({".ml"}),
    grammar_package="tree_sitter_mylang",       # PyPI package name
    scm_file="mylang.scm",                      # query file name
    heritage_node_types=frozenset({"class_declaration"}),
    entry_point_patterns=("main.ml",),
    manifest_files=("mylang.toml",),
    shebang_tokens=("mylang",),
    builtin_calls=frozenset({"print", "len"}),  # filter from call graph
    builtin_parents=frozenset({"Object"}),       # filter from heritage
    color_hex="#AB47BC",
)
```

### Step 2: Add the `LanguageTag`

Add `"mylang"` to the `LanguageTag` Literal type in
`packages/core/src/repowise/core/ingestion/models.py`.

### Step 3: Write a tree-sitter query file

Create `packages/core/src/repowise/core/ingestion/queries/mylang.scm` using
tree-sitter S-expression syntax. Follow the capture-name conventions:

| Capture | Purpose | Required? |
|---------|---------|-----------|
| `@symbol.def` | Full definition node (line numbers, kind lookup) | Yes |
| `@symbol.name` | Name identifier | Yes |
| `@symbol.params` | Parameter list | No |
| `@symbol.modifiers` | Decorators / visibility modifiers | No |
| `@symbol.receiver` | Go-style method receiver | No |
| `@import.statement` | Full import node | Yes |
| `@import.module` | Module path being imported | Yes |
| `@call.target` | Function/method being called | No (enables call graph) |
| `@call.receiver` | Object the call is made on | No |
| `@call.arguments` | Call arguments | No |

Look at existing `.scm` files for examples --- `python.scm` and
`typescript.scm` are good starting points.

### Step 4: Add a `LanguageConfig` entry

Add a parser configuration to `LANGUAGE_CONFIGS` in
`packages/core/src/repowise/core/ingestion/parser.py`:

```python
"mylang": LanguageConfig(
    symbol_node_types={
        "function_definition": "function",
        "class_definition": "class",
    },
    import_node_types=["import_statement"],
    export_node_types=[],
    visibility_fn=public_by_default,  # from extractors.visibility
    parent_extraction="nesting",
    parent_class_types=frozenset({"class_definition"}),
    entry_point_patterns=["main.ml"],
),
```

### Step 5: Add the tree-sitter grammar dependency

Add the grammar package to `pyproject.toml`:

```toml
[project]
dependencies = [
    # ...
    "tree-sitter-mylang>=0.23,<1",
]
```

### Step 6 (optional): Binding extractor

For full-tier support, add a `extract_mylang_bindings()` function in
`packages/core/src/repowise/core/ingestion/extractors/bindings.py` and
register it in the `extract_import_bindings()` dispatcher. Without this,
imports are still resolved but named-binding-level call resolution won't
work.

### Step 7 (optional): Heritage extractor

Add a `_extract_mylang_heritage()` function in
`packages/core/src/repowise/core/ingestion/extractors/heritage.py` and
register it in the `HERITAGE_EXTRACTORS` dict. Without this, inheritance
chains won't appear in the graph.

### Step 8 (optional): Import resolver

If the language has a non-trivial import system, create a resolver in
`packages/core/src/repowise/core/ingestion/resolvers/mylang.py` and
register it in the `_RESOLVERS` dict in `resolvers/__init__.py`. For simple
languages, the generic stem-map fallback (matching by filename) works out
of the box.

### Verify

```bash
# Run the parser tests
pytest tests/ -k "mylang or sample_repo" -x

# Index a real project
repowise init /path/to/mylang-project
```

No changes are needed to `traverser.py`, `dead_code.py`,
`page_generator.py`, `cost_estimator.py`, or any other consumer file ---
they all derive their language sets from the registry automatically.

---

## Architecture

The language pipeline is fully modular:

```
ingestion/
  languages/           # LanguageRegistry + LanguageSpec (identity data)
  extractors/          # Per-language AST extraction
    visibility.py      #   symbol visibility (public/private/protected)
    signatures.py      #   human-readable signature building
    docstrings.py      #   module + symbol docstring extraction
    bindings.py        #   import name + alias binding extraction
    heritage.py        #   inheritance/interface/trait extraction
  resolvers/           # Per-language import resolution
    python.py          #   dotted imports, __init__.py, src/ layout
    typescript.py      #   multi-ext probe, tsconfig aliases
    go.py              #   go.mod module path stripping
    rust.py            #   crate::/self::/super::, mod.rs probing
    cpp.py             #   compile_commands.json include paths
    kotlin.py          #   package-to-directory mapping
    ruby.py            #   require/require_relative resolution
    csharp.py          #   namespace-based resolution
    swift.py           #   module import resolution
    scala.py           #   package-to-directory mapping
    php.py             #   namespace/PSR-4 resolution
    generic.py         #   stem-matching fallback
  framework_edges.py   # Django, FastAPI, Flask, pytest detection
  parser.py            # ASTParser (language-agnostic orchestration)
  graph.py             # GraphBuilder (import/call/heritage resolution)
```

Adding a new language requires zero changes to `parser.py`, `graph.py`,
`traverser.py`, `dead_code.py`, or any other core file.

---

## Roadmap

| Language | Target Tier | Status |
|----------|------------|--------|
| Dart | Good | Planned — `tree-sitter-dart` available |
| Elixir | Good | Planned — `tree-sitter-elixir` available |
| F# | Good | Planned — `tree-sitter-f-sharp` available |
