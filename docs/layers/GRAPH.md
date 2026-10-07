# Graph Intelligence

Most tools that draw a code graph will tell you `A calls B`. Very few will tell
you how sure they are, and the interesting questions cannot be answered
without that.

repowise builds a two-tier graph of your codebase, files and symbols, with no
model calls and no network. It needs no LLM key and is built during
`repowise init` and kept current by `repowise update`. What makes it worth
trusting is not its size. Every edge carries its own evidence: the strategy
that produced it and the confidence that strategy earns.

<p>
  <img src="https://img.shields.io/badge/17-edge_types-3178C6?style=flat-square&labelColor=0A0A0A" alt="17 edge types" />
  <img src="https://img.shields.io/badge/39-resolution_origins-059669?style=flat-square&labelColor=0A0A0A" alt="39 resolution origins" />
  <img src="https://img.shields.io/badge/26-full--AST_languages-F59520?style=flat-square&labelColor=0A0A0A" alt="26 full-AST languages" />
  <img src="https://img.shields.io/badge/0-LLM_calls-1E293B?style=flat-square&labelColor=0A0A0A" alt="zero LLM calls" />
</p>

**Contents:** [Quick start](#quick-start) ·
[The problem with a plain arrow](#the-problem-with-a-plain-arrow) ·
[Two stages, and they fail differently](#two-stages-and-they-fail-differently) ·
[How good is it, and how we know](#how-good-is-it-and-how-we-know) ·
[What is in the graph](#what-is-in-the-graph) ·
[Every edge says how it got there](#every-edge-says-how-it-got-there) ·
[Typing the receiver](#typing-the-receiver) ·
[Seventeen edge types](#seventeen-edge-types-because-calls-was-doing-too-many-jobs) ·
[Flows that say why they stopped](#flows-that-say-why-they-stopped) ·
[What the graph powers](#what-the-graph-powers) ·
[Seeing it yourself](#seeing-it-yourself) ·
[Honest ceilings](#honest-ceilings)

---

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
edges below 0.7, which keeps
the repo-wide name guesses out of them (see
[how an agent should read confidence](#how-an-agent-should-read-confidence)).

---

## The problem with a plain arrow

Consider one line of Python:

```python
user.save()
```

To draw an edge, a tool has to answer "what is `user`?". There are three ways to
do it, and they are not equally good:

1. Give up. Emit nothing. The edge is missing, so a dead-code pass now thinks
   `save` is unused and offers to delete it.
2. Guess. Find every method named `save` in the repo and pick one. If your
   codebase has `User.save`, `Draft.save` and `Session.save`, you have a two in
   three chance of drawing an arrow to the wrong file.
3. Work out what `user` is, then resolve `save` on that type.

Most graphs do (2) and present the result identically to an edge they were
certain about. That is the actual problem. A wrong arrow is worse than a missing
one, because a missing arrow looks like missing information and a wrong arrow
looks like an answer.

repowise does (3) where it can, falls back to (2) where it must, and labels
which one happened on every edge.

---

## Two stages, and they fail differently

An edge is two claims made by two different pieces of machinery. They are kept
apart because they break in opposite directions.

**Capture** is noticing that a call was written at all. It is a tree-sitter
query per language, listing the source shapes that count as a call site:

```scheme
; Go method call: obj.Method(args)
(call_expression
  function: (selector_expression
    operand: (identifier) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site
```

A shape that no query matches is a call the graph will never contain. Not low
confidence, not unresolved: absent. Nothing downstream can recover it, because
nothing downstream knows the call was there. Go alone lists a plain call, a
method call, a package-qualified call, a chained call, and a function passed as
an argument. That last one is captured as a reference, not a call, because
passing a handler is not invoking it.

**Resolution** is working out what a captured name points at.
`repo.save(draft)` hands you the name `save` and a receiver spelled `repo`, and
the job is to turn that into one declaration in one file. This is where the 39
resolution origins live.

| Stage | Fails when | Costs you | How you find out |
|---|---|---|---|
| Capture | no query covers that call shape | recall: the edge does not exist | nothing internal can tell you; it takes an outside answer key |
| Resolution | the receiver cannot be typed, or the name is ambiguous | precision if it guesses, recall if it declines | the origin on the edge names the strategy, so a wrong class of edge is found and fixed once |

That asymmetry sets the design. A missed capture is silent, so it is measured
against a compiler. A bad resolution is loud, so every edge is stamped with the
strategy that produced it, and nothing is allowed to launder a guess into a
fact.

At the bottom of the ladder repowise declines to guess. That costs recall, and
it is the right way round.

---

## How good is it, and how we know

Measured on main, freshly indexed from source, on 2026-10-03.

- **Against a compiler, Go and TypeScript.** Seven cells over five
  repositories, 37,853 compiler edges (the Go team's RTA call graph and `tsc`'s
  own resolution). Precision runs 0.976 to 0.995, and recall is higher than our
  August figure in all seven cells. In all seven, no tool in the five-tool
  comparison that finds as much of the call graph gets more of it right. Two
  tools post higher precision in some cells by drawing much smaller graphs.
- **Against compiler indexes for more languages.** Java on jsoup reads 0.781
  precision against javac (0.725 to 0.781 across three repositories). C on git, a
  repository held out from all tuning, reads 0.979 precision at 0.83 recall.
  C#, C++, Rust and Python (against jedi, a static analyser) are in the same
  table.
- **Hand-graded across nine languages.** 280 call edges per tool, 560 in all,
  each read with its imports and enclosing scope open: 240 of 280 correct for
  repowise (85.7%) against 164 of 280 for CodeGraph 1.5.0 (58.6%), intervals
  disjoint. Read the other way round, about one call edge in seven (roughly
  fourteen percent) is wrong, concentrated in Rust, C++ and Java.

Recall is the column we do not lead everywhere. Against the compiler we lead
recall in three of the seven cells and trail in four. Where our misses go is
mostly dynamic dispatch, which no tool in the comparison has cleared without
emitting several wrong edges for each right one.

The per-language table, the five-tool table and the method are in
[BENCHMARKS.md: accuracy by language](../BENCHMARKS.md#accuracy-by-language) and
[BENCHMARKS.md: call graph against a compiler](../BENCHMARKS.md#8-the-same-question-against-an-answer-key-we-do-not-control).
The hand-graded rows are in
[BENCHMARKS.md: hand-graded](../BENCHMARKS.md#7-edge-precision).

---

## What is in the graph

**Two tiers of node.** Files and packages on one tier; functions, classes,
methods and interfaces on the other. Third-party packages appear as lightweight
external nodes, so a dependency is visible without being documented.

**Two families of edge.** Structure the parser can see (imports, calls,
inheritance) and structure only history can see (files that keep changing
together without importing each other). They are kept apart on purpose. A
co-change edge is real signal, but treating it as a dependency would put "these
two files were edited in the same commit" into your import graph.

Every consumer reads the graph through one of three shared views, so no two
features disagree about what counts as a dependency:

| View | Answers |
|------|---------|
| File dependency | "what does this file depend on?" Used for communities, cycles and coupling |
| Symbol use | "what reaches this symbol?" Containment is excluded, since a class holding a method is not a use of it |
| Reachability | "does anything use this at all?" The dead-code view |

---

## Every edge says how it got there

Each `calls` and `references` edge is stamped with a **resolution origin**: the
named strategy that produced it. There are 39, from a closed vocabulary, and
each one carries exactly one confidence. The table lists the 18 base origins;
the other 21 are receiver-typing variants, covered in
[Typing the receiver](#typing-the-receiver).

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

Because every origin has exactly one confidence, the origin distribution and the
confidence histogram are two views of the same data. That is what makes the
stamping checkable.

### How an agent should read confidence

| Confidence | Read it as |
|:---:|---|
| 0.93 to 0.95 | Established in the calling file or class. Treat as fact |
| 0.85 to 0.90 | Established through an import, a package or one ancestor. Safe to act on |
| 0.75 | A class and method name match somewhere in the repo. Verify before acting on it alone |
| 0.50 | A unique name, nothing more. A lead, not an answer |

An agent tracing a flow can decline anything below a threshold and know what it
declined. A reviewer reading a blast radius can tell "this definitely breaks"
from "this shares a method name with something that breaks". When the graph is
wrong, the origin names which strategy was wrong, so it is fixed once for every
call site that strategy touched.

---

## Typing the receiver

21 of the 39 origins exist to answer the `user.save()` question properly. They
read the receiver's declaration and resolve the method on that type, instead of
matching a bare method name.

```java
void handle(UserRepo repo) {     // parameter declares the type
    var draft = new Draft();     // constructor declares the type
    repo.save(draft);            // -> UserRepo.save, not Draft.save
    this.cache.evict(draft.id);  // field on the enclosing class
}
```

Each receiver shape is its own origin family, so each can be measured on its
own:

| Family | Example | Origins |
|---|---|:---:|
| Locals and parameters, including Go's method receiver | `var repo = new UserRepo()`, `func (s *Server) handle()` | 4 |
| Fields of the enclosing class | `this.cache.evict(...)` | 4 |
| Framework-retyped symbols | `@shared_task def add` makes `add` a `Task`, so `add.s()` is `Task::s` | 4 |
| C# extension methods, via the type their `this` parameter names | `items.Paged(10)` | 3 |
| Dotted receivers typed hop by hop through declared fields | `this.a.b.m()` | 2 |
| The declared return type of an inner call | `repo.find(id).save()` | 4 |

The families map onto the same scopes as the base origins (same file, same
package, import, global), and a typed origin shares its untyped twin's
confidence. The inferred type had to declare the method before any edge was
emitted, so the evidence is no weaker. What differs is how the receiver was
named, and naming that difference is the point of an origin.

Declaration scanning covers Java, C#, Python, Go, Kotlin, Swift, TypeScript,
Rust and C++. A language outside that list falls back to the base origins and
says so on every edge.

---

## Seventeen edge types, because `calls` was doing too many jobs

An edge type is a claim. If one type carries several different claims, every
consumer downstream has to guess which one it is looking at. The graph has 17
edge types. These six carry the distinctions that matter most when you read an
answer:

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
`dynamic_imports` and `dynamic_url_route`.

The `references` distinction is not academic. A handler sitting in a dispatch
table is never called anywhere a parser can see. Counting that as "no use"
would report entire registration layers as safe to delete.

`framework_binds` is separated from `calls` for the opposite reason. A fixture
nobody calls and a collaborator nobody constructs are both used, by the
container. But an inferred wiring hop is not source, and letting it render as a
call would put it into an execution flow as though someone had written it.

---

## Flows that say why they stopped

An execution flow walks the call graph from an entry point. Every walk ends, and
a trace that just stops reads the same whether execution really ends there or
the walker ran out of things it could follow.

So every flow ends with one of six reasons:

| Termination | Meaning |
|---|---|
| `no_callees` | No outgoing call edges recorded |
| `cycle` | Every successor was already on this trace: recursion or mutual calls |
| `depth_limit` | The hop budget ran out. Nothing is known beyond it |
| `confidence_filtered` | Every successor sat below the confidence floor |
| `excluded_target` | Every successor was a test, demo or fixture node |
| `callees_truncated` | Rows were cut before the walk saw them |

Two details carry most of the value. `no_callees` is deliberately not called a
leaf: a symbol whose calls we failed to resolve looks exactly like a function
that calls nothing, and asserting the second is a claim the graph cannot
support. When a confidence floor stopped the walk, the flow reports which
origins it declined, which is the part you can act on.

---

## What the graph powers

The graph is not the product. These are:

- **Blast radius.** Change a file, walk the dependency edges, get the set of
  things that can break. Confidence travels with it, so a speculative hop is
  visible as one.
- **Dead code.** Reachability over the union view, not over `calls` alone. A
  symbol reached only by a framework, a dispatch table or a type reference is
  not dead, and each of those is a different edge type for this reason.
- **Communities.** Leiden clustering (Louvain as fallback) finds the modules
  your codebase actually has, which is often not the directory layout.
- **Centrality.** PageRank over the file tier ranks what everything depends on.
  Betweenness finds the bridges whose removal splits the graph. Neither is fed
  co-change edges, because "changes alongside many things" is not "many things
  depend on it".
- **Cycles.** Strongly connected components, which need their own documentation
  order because nothing in them can be explained before the others.
- **Execution flows.** Entry point to leaf, ranked, with the termination reason
  attached.
- **Framework wiring.** Framework handlers connect routes to handlers, DI
  registrations to implementations and ORM entities to their relationships,
  across Django, FastAPI, Flask, Spring, ASP.NET, Rails, Laravel, Next.js,
  Express, Axum, Gin and more.

---

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

**Across a multi-repo workspace**, a service-level system graph built from
contracts and package dependencies adds declared dependency rules, cycle
detection, a dependency-structure matrix and a 1-10 architecture score. Those
rules work between services, not between layers inside one repository. See
[Architecture Conformance](../scale/WORKSPACES.md#architecture-conformance) and
[Architecture Metrics](../scale/WORKSPACES.md#architecture-metrics).

**Everything above is computed without a model call.** An LLM is an optional
upgrade for prose quality in the wiki. It is never part of building the graph,
which is why the graph is reproducible and why indexing needs no API key.

---

## Honest ceilings

- **Resolution quality varies by language.** The
  [language support page](LANGUAGE_SUPPORT.md) says what each rung covers.
  Statically typed languages with explicit declarations resolve best.
  Dynamically typed and reflective code resolves worst, and falls back to the
  low-confidence origins.
- **Receiver typing reads declarations with per-language patterns**, not a full
  type checker. A declaration the patterns cannot see is a receiver that does
  not get typed, and the call falls back to a weaker origin.
- **`dispatches_to` compares no signature.** It matches by method name, so it
  names a possible dispatch target, not a proven override.
- **A repo-wide unique name is still a guess**, and `global_unique` at 0.50 is
  where that lives. It is kept because a labelled guess beats a missing edge for
  reachability, and it is labelled so nothing downstream mistakes it for a fact.
- **Unused methods are not reported**, for any language. Method-level precision
  did not hold up when measured, and shipping it would mean confidently
  recommending wrong deletions.
- **About fourteen percent of our call edges are wrong** on the hand-graded
  sample (85.7% correct over 280 rows), and we trail on recall in four of seven
  compiler-graded cells.
- **Overloads cost precision on Java and C#.** The graph keeps one node per
  overload set, so a call to the right method can land on the wrong overload.
  On C#, plan against the compiler figure (0.73 to 0.92), not the hand-graded
  30 of 30.
- **Precision is published for ten languages, not all 26.** Go, TypeScript,
  Java, C#, C, C++, Rust and Python have a compiler or analyser answer key.
  Kotlin and Swift have the hand-graded sample only, at 30 rows each. The other
  full-AST languages have no published precision figure.
- **A precision figure without the recall beside it is a misuse of this data**,
  including by us. Two tools beat us on precision in some compiler cells, each by
  drawing a much smaller graph.

---

## See also

- [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) · what works per language, and the tier each one sits in
- [architecture/language-support.md](../architecture/language-support.md) · call resolution internals and the contributor recipe
- [architecture/graph-algorithms.md](../architecture/graph-algorithms.md) · PageRank, Leiden, betweenness and SCC in detail
- [DEAD_CODE.md](DEAD_CODE.md) · how reachability becomes a confidence-tiered report
- [CHANGE_RISK.md](CHANGE_RISK.md) · how live diff-shape review and structural PR impact stay distinct
- [reference/COMPUTED_GLOSSARY.md](../reference/COMPUTED_GLOSSARY.md) · every derived metric, defined
- [BENCHMARKS.md](../BENCHMARKS.md#accuracy-by-language) · the accuracy numbers on this page, with sample sizes, intervals and method
- [repowise-bench/graph](https://github.com/repowise-dev/repowise-bench/tree/master/graph) · the harnesses, the graded rows and the pre-registrations
