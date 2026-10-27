# Graph Intelligence

repowise builds a two-tier graph of your codebase, files and symbols, with no
model calls and no network. It needs no LLM key, is built during
`repowise init` and is kept current by `repowise update`. Every edge carries its
own evidence: the strategy that produced it and the confidence that strategy
earns, so an agent or a reviewer can tell a fact from a guess. An LLM is only
an optional upgrade for wiki prose, so the graph is reproducible.

<p>
  <img src="https://img.shields.io/badge/17-edge_types-3178C6?style=flat-square&labelColor=0A0A0A" alt="17 edge types" />
  <img src="https://img.shields.io/badge/39-resolution_origins-059669?style=flat-square&labelColor=0A0A0A" alt="39 resolution origins" />
  <img src="https://img.shields.io/badge/28-full--AST_languages-F59520?style=flat-square&labelColor=0A0A0A" alt="28 full-AST languages" />
  <img src="https://img.shields.io/badge/0-LLM_calls-1E293B?style=flat-square&labelColor=0A0A0A" alt="zero LLM calls" />
</p>

**Contents:** [Quick start](#quick-start), [How an edge is made](#how-an-edge-is-made),
[How far to trust it](#how-far-to-trust-it), [What is in the graph](#what-is-in-the-graph),
[Every edge says how it got there](#every-edge-says-how-it-got-there),
[Typing the receiver](#typing-the-receiver), [Edge types](#edge-types),
[Flows that say why they stopped](#flows-that-say-why-they-stopped),
[What the graph powers](#what-the-graph-powers), [Seeing it yourself](#seeing-it-yourself),
[Limits](#limits)

## Quick start

```bash
repowise init                                    # builds the graph; no API key needed
repowise context src/app.py --include callers    # who depends on this file, with confidence
repowise serve                                   # dashboard: Graph, Architecture, Coupling, Blast radius
```

From an agent, over MCP:

```text
get_context(targets=["src/app.py::App.run"], include=["callers"])
get_symbol(id="src/app.py::App.run", depth=2)
```

`get_context` lists callers and callees with their confidence; `get_symbol` at
depth 2 also returns the bodies the symbol calls. Both expansions drop call
edges below 0.7, which keeps the repo-wide name guesses out of them (see
[how an agent should read confidence](#how-an-agent-should-read-confidence)).

## How an edge is made

To draw an edge for `user.save()`, a tool has to know what `user` is.
repowise reads the receiver's declared type and resolves `save` on that type.
Where it cannot, it falls back to matching the name across the repo, and the
edge says which of the two happened. A wrong arrow that looks like an answer is
worse than a labelled guess, so nothing turns a guess into a fact.

An edge is two claims, made in two stages that fail in different ways:

| Stage | What it does | Fails when | Costs you |
|---|---|---|---|
| Capture | A per-language tree-sitter query notices that a call was written | no query covers that call shape | recall: the edge does not exist |
| Resolution | Turns the captured name and receiver into one declaration in one file; the 39 resolution origins live here | the receiver cannot be typed, or the name is ambiguous | precision if it guesses, recall if it declines |

A missed capture is silent, so it is measured against a compiler. A bad
resolution is visible, because the origin on the edge names the strategy, so a
wrong class of edge is found and fixed once. At the bottom of the ladder
repowise declines to guess, trading recall for precision. The design and the
capture queries are in
[architecture/graph-algorithms.md](../architecture/graph-algorithms.md#call-edges-capture-resolution-and-measurement).

## How far to trust it

| Answer key | Scope | Result |
|---|---|---|
| Compiler (Go RTA call graph, `tsc`) | Go and TypeScript, 7 cells over 5 repositories, 37,853 compiler edges | Precision 0.976 to 0.995 |
| javac | Java, 3 repositories | Precision 0.725 to 0.781 (0.781 on jsoup) |
| Compiler index | C on git, held out from all tuning | Precision 0.979 at 0.83 recall |
| Hand-graded | 280 call edges across nine languages | 240 of 280 correct (85.7%); errors cluster in Rust, C++ and Java |

In all seven Go and TypeScript cells, no tool in the five-tool comparison that
finds as much of the call graph gets more of it right; two tools post higher
precision in some cells by drawing much smaller graphs. Recall leads in three of
the seven cells, and most misses are dynamic dispatch. C#, C++, Rust and Python
(against jedi, a static analyser) are measured in the same table.

Per-language figures, the five-tool table and sample sizes:
[BENCHMARKS.md: accuracy by language](../BENCHMARKS.md#accuracy-by-language),
[call graph against a compiler](../BENCHMARKS.md#call-graph-against-a-compiler-five-tools)
and [hand-graded](../BENCHMARKS.md#call-edges-hand-graded-across-nine-languages).

## What is in the graph

**Two tiers of node.** Files and packages on one tier; functions, classes,
methods and interfaces on the other. Third-party packages appear as lightweight
external nodes, so a dependency is visible without being documented.

**Two families of edge.** Structure the parser can see (imports, calls,
inheritance) and structure only history can see (files that keep changing
together without importing each other). They are kept apart, so a shared commit
never appears in your import graph as a dependency.

Every consumer reads the graph through one of three shared views, so no two
features disagree about what counts as a dependency:

| View | Answers |
|------|---------|
| File dependency | "what does this file depend on?" Used for communities, cycles and coupling |
| Symbol use | "what reaches this symbol?" Containment is excluded, since a class holding a method is not a use of it |
| Reachability | "does anything use this at all?" The dead-code view |

## Every edge says how it got there

Each `calls` and `references` edge is stamped with a **resolution origin**: the
named strategy that produced it. There are 39, from a closed vocabulary, and
each one carries exactly one confidence, so the origin distribution and the
confidence histogram are two views of the same data. The table lists the 18
base origins; the other 21 are [receiver-typing variants](#typing-the-receiver).

| Confidence | Origin | What was established |
|:---:|---|---|
| 0.95 | `same_file` | The callee is defined in the calling file |
| 0.95 | `self_scope` | `self` / `this`: a method on the caller's own class |
| 0.95 | `enclosing_class` | A bare call, bound to the caller's own class |
| 0.93 | `receiver_same_file` | The receiver names a type declared in this file |
| 0.93 | `scoped_name` | C/C++ `Qualifier::name()`: the class is written at the call site |
| 0.90 | `import_scoped` | The name was imported from the file that defines it |
| 0.90 | `same_package` | A sibling file that needs no import (Go, JVM) |
| 0.90 | `receiver_same_package` | The receiver is a class in the same package (JVM) |
| 0.90 | `self_inherited` | `self` / `this` or Python `super()`, found on exactly one ancestor |
| 0.90 | `enclosing_inherited` | A bare call, found on exactly one ancestor |
| 0.88 | `package_alias` | Go `pkg.Func`, resolved across the whole package |
| 0.88 | `module_alias` | The receiver is an imported module |
| 0.88 | `crate_root` | A Rust crate-scoped reference |
| 0.88 | `receiver_import` | The receiver's type was found in an imported file |
| 0.85 | `import_merged` | It is in one of the imported files; which one is unattributed |
| 0.85 | `same_target` | C/C++: a sibling translation unit of the same build target |
| 0.75 | `receiver_global` | The `(class, method)` pair exists somewhere in the repo |
| 0.50 | `global_unique` | The name is unique repo-wide. A guess, and stored as one |

### How an agent should read confidence

| Confidence | Read it as |
|:---:|---|
| 0.93 to 0.95 | Established in the calling file or class. Treat as fact |
| 0.85 to 0.90 | Established through an import, a package or one ancestor. Safe to act on |
| 0.75 | A class and method name match somewhere in the repo. Verify before acting on it alone |
| 0.50 | A unique name, nothing more. A lead, not an answer |

An agent tracing a flow can decline anything below a threshold and know what it
declined. A reviewer reading a blast radius can tell "this definitely breaks"
from "this shares a method name with something that breaks".

## Typing the receiver

21 of the 39 origins read the receiver's declaration and resolve the method on
that type, instead of matching a bare method name.

```java
void handle(UserRepo repo) {     // parameter declares the type
    var draft = new Draft();     // constructor declares the type
    repo.save(draft);            // -> UserRepo.save, not Draft.save
    this.cache.evict(draft.id);  // field on the enclosing class
}
```

| Family | Example | Origins |
|---|---|:---:|
| Locals and parameters, including Go's method receiver | `var repo = new UserRepo()`, `func (s *Server) handle()` | 4 |
| Fields of the enclosing class | `this.cache.evict(...)` | 4 |
| Framework-retyped symbols | `@shared_task def add` makes `add` a `Task`, so `add.s()` is `Task::s` | 4 |
| C# extension methods and Kotlin extension functions, via the type they extend or one of its ancestors | `items.Paged(10)`, `helper.logScreenView(name)` | 3 |
| Dotted receivers typed hop by hop through declared fields | `this.a.b.m()` | 2 |
| The declared return type of an inner call | `repo.find(id).save()` | 4 |

The families map onto the same scopes as the base origins (same file, same
package, import, global), and a typed origin shares its untyped twin's
confidence: the inferred type had to declare the method before any edge was
emitted. Declaration scanning covers Java, C#, Python, Go, Kotlin, Swift,
TypeScript, Rust and C++. Other languages use the base origins and say so on
every edge.

## Edge types

The graph has 17 edge types, each one a distinct claim. These six matter most
when you read an answer:

| Edge | Claims | Does not claim |
|---|---|---|
| `calls` | The parser saw a call expression and resolved its callee | |
| `references` | Something holds a handle to this function: a dispatch-table entry, a callback field, a registration macro | That it is ever invoked. Enough to make deleting it unsafe, not enough to call it a call |
| `dispatches_to` | A base method points at an implementation that could answer for it | A proven override. No signature is compared |
| `framework_binds` | A framework wires these two symbols together: a pytest fixture, a Spring injection | A call. Nothing here is source a parser could have seen |
| `type_use` | A type is referenced in a constructor, method, delegate or record parameter | An import. Weighted below one |
| `co_changes` | These files keep changing in the same commit | Any code dependency at all |

The other eleven are `imports`, `defines`, `has_method`, `extends`,
`implements`, `method_implements`, `framework`, `reads`, `dynamic_uses`,
`dynamic_imports` and `dynamic_url_route`. A `references` edge keeps
registration layers out of dead-code reports, and a `framework_binds` hop counts
as a use without appearing in an execution flow as a call.

## Flows that say why they stopped

An execution flow walks the call graph from an entry point, and every flow ends
with one of six reasons:

| Termination | Meaning |
|---|---|
| `no_callees` | No outgoing call edges recorded |
| `cycle` | Every successor was already on this trace: recursion or mutual calls |
| `depth_limit` | The hop budget ran out. Nothing is known beyond it |
| `confidence_filtered` | Every successor sat below the confidence floor |
| `excluded_target` | Every successor was a test, demo or fixture node |
| `callees_truncated` | Rows were cut before the walk saw them |

`no_callees` is not called a leaf: a symbol whose calls were not resolved looks
the same as a function that calls nothing. When a confidence floor stopped the
walk, the flow reports which origins it declined.

## What the graph powers

| Feature | What the graph contributes |
|---|---|
| Blast radius | The set of things a change can break, with confidence on every hop |
| Dead code | Reachability over the union view, so a symbol reached only by a framework, a dispatch table or a type reference is not dead |
| Communities | Leiden clustering (Louvain as fallback) finds the modules your codebase actually has, often not the directory layout |
| Centrality | PageRank over the file tier ranks what everything depends on; betweenness finds the bridges. Neither is fed co-change edges |
| Cycles | Strongly connected components, documented together because nothing in them can be explained before the others |
| Execution flows | Entry point to leaf, ranked, with the termination reason attached |
| Framework wiring | Routes to handlers, DI registrations to implementations, ORM entities to relationships, across Django, FastAPI, Flask, Spring, ASP.NET, Rails, Laravel, Next.js, Express, Axum, Gin and more |

The algorithms behind these are in
[architecture/graph-algorithms.md](../architecture/graph-algorithms.md).

## Seeing it yourself

**For your agent**, over MCP:

| Tool | Gives you |
|---|---|
| `get_context(targets)` | Dependencies, dependents and co-change partners for a file or symbol; `include=["callers"]` for callers with confidence |
| `get_symbol(id, depth)` | One symbol's verified body; depth 2-3 adds the bodies it calls |
| `get_risk(targets)` | What history and the graph say about touching these paths |
| `get_dead_code()` | Unreachable files, unused exports and zombie packages, by confidence tier |
| `get_execution_flows()` | Traced flows with their termination reason (opt-in) |
| `get_blast_radius()` | Cross-repo impact, in workspace mode (opt-in) |

Opt-in tools are enabled through the tool surface settings; see
[MCP_TOOLS.md](../agent/MCP_TOOLS.md#get_execution_flows).

**In the dashboard**, `repowise serve` gives you the Graph, Architecture,
Coupling, Blast radius, Knowledge graph and Dead code views.

**Across a multi-repo workspace**, a service-level system graph adds declared
dependency rules between services, cycle detection, a dependency-structure matrix
and a 1-10 architecture score; see
[Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance) and
[Architecture Metrics](../scale/WORKSPACES.md#architecture-metrics).

## Limits

- Statically typed languages with explicit declarations resolve best;
  dynamically typed and reflective code falls back to the low-confidence origins.
- Receiver typing reads declarations with per-language patterns, not a full
  type checker, so a declaration the patterns cannot see leaves a weaker origin.
- `dispatches_to` compares no signature: a possible target, not a proven override.
- `global_unique` at 0.50 is a labelled guess, kept for reachability.
- Unused methods are not reported for any language, because method-level
  precision is too low to recommend deletions.
- About one call edge in seven is wrong on the hand-graded sample (85.7%
  correct over 280 rows). Recall is lower than the leading tool in four of seven
  compiler-graded cells.
- The graph keeps one node per overload set, so on Java and C# a call can land
  on the wrong overload. On C#, plan against the compiler figure (0.73 to 0.92),
  not the hand-graded 30 of 30.
- Precision is published for eleven of the 28 full-AST languages. Go, TypeScript,
  Java, C#, C, C++, Rust and Python have a compiler or analyser answer key;
  Kotlin and Swift have the hand-graded sample only, at 30 rows each, and Apex
  90 hand-graded rows with recall against a census of literal references.
- Read a precision figure together with its recall. Two tools post higher
  precision in some compiler cells, each by drawing a much smaller graph.

## See also

- [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md): what works per language, and the tier each one sits in
- [architecture/language-support.md](../architecture/language-support.md): call resolution internals and the contributor recipe
- [architecture/graph-algorithms.md](../architecture/graph-algorithms.md): PageRank, Leiden, betweenness and SCC, plus how call edges are captured and measured
- [DEAD_CODE.md](DEAD_CODE.md): how reachability becomes a confidence-tiered report
- [CHANGE_RISK.md](CHANGE_RISK.md): how live diff-shape review and structural PR impact stay distinct
- [reference/COMPUTED_GLOSSARY.md](../reference/COMPUTED_GLOSSARY.md): every derived metric, defined
- [BENCHMARKS.md](../BENCHMARKS.md#accuracy-by-language): the accuracy numbers on this page, with sample sizes, intervals and method
- [repowise-bench/graph](https://github.com/repowise-dev/repowise-bench/tree/master/graph): the harnesses, the graded rows and the pre-registrations
