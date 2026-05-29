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
- **assertion_blocks** — runs of ≥ 2 consecutive assertion statements,
  each `(start_line, end_line, count)`. Opt-in per language via
  `assert_kinds` / `assert_call_kinds`. Feeds the test-quality biomarkers
  (`large_assertion_block`, `duplicated_assertion_block`).

It also emits **class-level** aggregates (`ClassComplexity`) for languages
that opt in — see "Class-level metrics" below.

## Performance characteristics

One parser instance per process (lazy-loaded via the ingestion registry).
Single pass per file — no AST re-traversal. The walker re-parses the file
because `ParsedFile` does not carry the tree-sitter `Node` across the
ingestion boundary; cost is acceptable (≲ 1 ms for typical files).

## Public API

```python
from repowise.core.analysis.health.complexity import (
    FileComplexity, FunctionComplexity, walk_file, walk_file_complexity,
)

# Full result: per-function AND per-class metrics.
fcx: FileComplexity = walk_file(abs_path, language, source_bytes)
fcx.functions  # list[FunctionComplexity]
fcx.classes    # list[ClassComplexity]

# Back-compat shortcut for callers that only need functions:
results: list[FunctionComplexity] = walk_file_complexity(
    abs_path, language, source_bytes
)
```

## Class-level metrics (LCOM4 / god-class)

`walk_file` also emits a `ClassComplexity` per class-like node for
languages whose `LanguageNodeMap` opts in (`class_kinds` non-empty):

| Field | Meaning |
|-------|---------|
| `method_count`, `total_nloc` | size of the class / impl block |
| `methods` | the same `FunctionComplexity` rows from the function pass |
| `max_method_ccn` | peak method complexity |
| `field_count` | distinct instance members referenced (best-effort) |
| `lcom4` | **LCOM4 cohesion** — connected components over the methods |

**LCOM4** builds a graph whose nodes are the class's methods and adds an
edge between two methods when they (a) reference a common instance field
or (b) one calls the other (a call surfaces as a reference to the callee's
name). `lcom4` is the number of connected components: `1` is fully
cohesive; `≥ 2` means the class splinters into unrelated method groups.
Consumed by `low_cohesion` and `god_class`.

### Heuristic limits (read before trusting a number)

- **Member detection is best-effort and per-language.** A method's field
  reads and intra-class calls are found by scanning for `self.x` /
  `this.x` / `$this->x` member-access nodes whose receiver token is in the
  language's `self_identifiers`. Members accessed without an explicit
  receiver (Python has none; Ruby's `@ivar`, implicit-`this` in some
  languages) are not counted.
- **Safety valve.** If a class yields *zero* detected member references
  (a pure-static utility class, or a language whose member-access node
  type isn't mapped yet), `lcom4` falls back to `1` ("no signal") rather
  than `len(methods)`. This means an unmapped language produces **no**
  `low_cohesion` finding rather than a false-positive flood — adding a new
  language can only ever turn signal *on*, never produce noise.
- **Constructors bridge clusters.** A constructor that initialises every
  field links all methods that use those fields, lowering LCOM4. This is
  inherent to LCOM4, not a bug.
- **Rust groups by `impl` block.** A type with several `impl` blocks
  yields several `ClassComplexity` rows (one per block). **Go** emits no
  classes (methods attach to a type via an external receiver, so there is
  no single grouping node).

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

To also get **class-level** metrics for that language, set three more
fields on its `LanguageNodeMap` (all default to empty = opt-out):

- `class_kinds` — node type(s) that group methods (a class body, or
  Rust's `impl` block).
- `self_identifiers` — the receiver token(s) for the instance (`self`,
  `this`, `$this`, `cls`).
- `member_access_kinds` — node type(s) for `receiver.member` access (both
  field reads and method calls).

The receiver and member-name children are extracted by a generic
field-name probe (`walker._self_member_name`), which tries the
`object`/`value` and `property`/`attribute`/`field`/`name` fields and then
falls back to positional children — so most tree-sitter grammars need only
the node-type names above. Get one wrong and the safety valve degrades the
class to "no signal" rather than emitting a false positive, so it's cheap
to add a language speculatively and refine later.

For **assertion-block** detection (test-quality smells), set two more
opt-in fields:

- `assert_kinds` — statement node type(s) that are assertions on their own
  (Python/Java `assert_statement`).
- `assert_call_kinds` — call node type(s) to inspect for an assertion
  *call*. A statement counts when its expression is a call of one of these
  kinds whose callee name starts with `assert` or `expect` (covers
  `assertEqual` / `assert_eq!` / `expect(...).toBe(...)`).

A language that maps neither produces no assertion blocks — never a false
positive.

Ships control-flow mappings for Python, TypeScript, JavaScript, Go, Java,
Rust; class-level mappings for Python, TypeScript, JavaScript, Java, Rust
(Go has no class-grouping node); assertion mappings for Python, TypeScript,
JavaScript, Java, Rust, Go. Adding more languages — any tier — is purely
additive in `languages.py`.
