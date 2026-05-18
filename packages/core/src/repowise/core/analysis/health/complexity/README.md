# complexity/

Tree-sitter AST walker. Single AST pass per file computes:

- **CCN** — McCabe cyclomatic complexity. Counts branching constructs
  (`if`, `for`, `while`, `case`, `catch`) plus boolean operators
  (`&&`, `||`).
- **max nesting depth** — deepest nested control-flow block per function.
- **cognitive complexity** — SonarSource-style weighted nesting cost
  (each level adds an incrementing penalty, plus +1 for each break in
  control flow).
- **bumps** — count of branches at the same nesting depth as their
  sibling branches. Feeds `bumpy_road`.
- **param_count** — formal parameter count. Feeds `primitive_obsession`.
- **nloc** — non-comment lines of code per function.

## Performance characteristics

One parser instance per process (lazy-loaded via the ingestion registry).
Single pass per file — no AST re-traversal. The walker re-parses the file
because `ParsedFile` does not carry the tree-sitter `Node` across the
ingestion boundary; cost is acceptable (≲ 1 ms for typical files).

## Public API

```python
from repowise.core.analysis.health.complexity import (
    FunctionComplexity, walk_file_complexity,
)

results: list[FunctionComplexity] = walk_file_complexity(
    abs_path, language, source_bytes
)
```

## Inputs

`abs_path` (filesystem path), `language` (e.g. `"python"`), `source_bytes`
(file content as bytes — `tree_sitter` requires bytes).

## Outputs

One `FunctionComplexity` per function/method symbol detected. Caller maps
back to ingestion `Symbol` objects by overlapping line ranges.

## Extension points

`languages.py` maps each language's tree-sitter **control-flow node-type
names** (e.g. `if_statement`, `for_expression`, `try_block`) to the
walker's abstract `BRANCH` / `LOOP` / `TRY` / `BOOLEAN_OP` categories.

Add a new language → one new `LanguageNodeMap` dict (~20 lines). No
`.scm` file edits required — those are owned by the ingestion parser.

Phase 1 ships mappings for Python, TypeScript, JavaScript, Go, Java, Rust.
Phase 5 adds C, C++, C#, Kotlin, Ruby, PHP, Swift, Scala.
