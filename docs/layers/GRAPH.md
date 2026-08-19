# Graph Intelligence

Most tools that draw a code graph will tell you `A calls B`. Very few will tell
you *how sure they are*, and none of the interesting questions can be answered
without that.

repowise builds a two-tier graph of your codebase, files and symbols, with no
model calls and no network. What makes it worth trusting is not its size. It is
that **every edge carries its own evidence**.

<p>
  <img src="https://img.shields.io/badge/17-edge_types-3178C6?style=flat-square&labelColor=0A0A0A" alt="17 edge types" />
  <img src="https://img.shields.io/badge/29-resolution_origins-059669?style=flat-square&labelColor=0A0A0A" alt="29 resolution origins" />
  <img src="https://img.shields.io/badge/19-languages-F59520?style=flat-square&labelColor=0A0A0A" alt="19 languages" />
  <img src="https://img.shields.io/badge/22-framework_detectors-7F52FF?style=flat-square&labelColor=0A0A0A" alt="22 framework detectors" />
  <img src="https://img.shields.io/badge/0-LLM_calls-1E293B?style=flat-square&labelColor=0A0A0A" alt="zero LLM calls" />
</p>

**Contents:** [The problem with a plain arrow](#the-problem-with-a-plain-arrow) ·
[What is in the graph](#what-is-in-the-graph) ·
[Every edge says how it got there](#every-edge-says-how-it-got-there) ·
[Typing the receiver](#typing-the-receiver) ·
[Seventeen edge types](#seventeen-edge-types-because-calls-was-doing-too-many-jobs) ·
[Flows that say why they stopped](#flows-that-say-why-they-stopped) ·
[What the graph powers](#what-the-graph-powers) ·
[Seeing it yourself](#seeing-it-yourself) ·
[Honest ceilings](#honest-ceilings)

---

## The problem with a plain arrow

Consider one line of Python:

```python
user.save()
```

To draw an edge, a tool has to answer "what is `user`?". There are three ways to
do it, and they are not equally good:

1. **Give up.** Emit nothing. The edge is missing, so a dead-code pass now
   thinks `save` is unused and offers to delete it.
2. **Guess.** Find every method named `save` in the repo and pick one. If your
   codebase has `User.save`, `Draft.save` and `Session.save`, you have a two in
   three chance of drawing an arrow to the wrong file.
3. **Work out what `user` is**, then resolve `save` on that type.

Most graphs do (2) and present the result identically to an edge they were
certain about. That is the actual problem. A wrong arrow is worse than a missing
one, because a missing arrow looks like missing information and a wrong arrow
looks like an answer.

repowise does (3) where it can, falls back to (2) where it must, and **labels
which one happened, every time**.

---

## What is in the graph

**Two tiers of node.** Files and packages on one tier; functions, classes,
methods and interfaces on the other. Third-party packages appear as lightweight
external nodes so a dependency is visible without being documented.

**Two families of edge.** Structure the parser can see (imports, calls,
inheritance) and structure only history can see (files that keep changing
together without importing each other). They are kept apart on purpose: a
co-change edge is real signal, but treating it as a dependency would put
"these two files were edited in the same commit" into your import graph.

Consumers never hand-roll a filter over this. Three named views are derived
from the vocabulary and shared:

| View | Answers |
|------|---------|
| `FILE_DEPENDENCY_EDGE_TYPES` | "what does this file depend on?" Used for communities, cycles, coupling |
| `SYMBOL_USE_EDGE_TYPES` | "what reaches this symbol?" Containment excluded, since a class holding a method is not a use of it |
| `REACHABILITY_USE_EDGE_TYPES` | "does anything use this at all?" The dead-code view |

Before those views existed, four different places each wrote their own edge
filter, and two of them silently counted co-change edges as imports.

---

## Every edge says how it got there

Each `calls` edge is stamped with a **resolution origin**: the named strategy
that produced it. There are 29, drawn from a closed vocabulary, and each one
carries exactly one confidence.

| Confidence | Origin | What was actually established |
|:---:|---|---|
| **0.95** | `same_file` | The callee is defined in the calling file. A certainty |
| **0.95** | `self_scope` | `self` / `this`, a method on the caller's own class |
| **0.93** | `receiver_same_file` | The receiver names a type declared right here |
| **0.90** | `import_scoped` | The name was imported from the file that defines it |
| **0.90** | `same_package` | A sibling file that needs no import (Go, JVM) |
| **0.88** | `receiver_import` | The receiver's type was found in an imported file |
| **0.85** | `import_merged` | It is in *one* of the imported files. Which one is unattributed |
| **0.75** | `receiver_global` | The `(class, method)` pair exists somewhere in the repo |
| **0.50** | `global_unique` | The name is unique repo-wide. **A guess, and stored as one** |

Because every origin has exactly one confidence, the origin distribution and the
confidence histogram are two views of the same data, which is what makes the
stamping checkable rather than decorative.

**Why this matters in practice.** An agent tracing an execution flow can decline
anything below a threshold and know what it declined. A reviewer looking at a
blast radius can tell "this definitely breaks" from "this shares a method name
with something that breaks". And when the graph is wrong, the origin tells you
*which strategy* was wrong, so it can be fixed once rather than patched per
call site.

---

## Typing the receiver

Twelve of the 29 origins exist to answer the `user.save()` question properly.
Rather than matching a bare method name, repowise reads the receiver's
**declaration** and resolves the method on that type.

```java
void handle(UserRepo repo) {     // parameter declares the type
    var draft = new Draft();     // constructor declares the type
    repo.save(draft);            // -> UserRepo.save, not Draft.save
    this.cache.evict(draft.id);  // field on the enclosing class
}
```

Five receiver shapes are covered, each a separate origin family so each can be
measured on its own:

| Shape | Example |
|---|---|
| Locals and parameters | `var repo = new UserRepo()`, `fun f(r: UserRepo)` |
| Fields of the enclosing class | `this.cache.evict(...)` |
| Freshly constructed receivers | `new Foo().bar()` |
| The method's own receiver | Go's `func (s *Server) handle()` |
| Framework-retyped symbols | `@shared_task def add` makes `add` a `Task`, so `add.s()` is `Task::s` |

Shipped for **Java, C#, Python, Go, Kotlin and Swift**. The typed origins share
their untyped twin's confidence deliberately: the inferred type had to declare
the method before any edge was emitted, so the evidence is no weaker. What
differs is how the receiver was named, and naming that difference is the entire
point of an origin.

Languages are added here one at a time, each gated on a sampled precision audit
before it ships. Several are deliberately absent because they failed that gate.
An unsupported language falls back to the weaker origins and says so, which is
the correct degradation.

---

## Seventeen edge types, because `calls` was doing too many jobs

An edge type is a claim. If one type carries several different claims, every
consumer downstream has to guess which one it is looking at.

| Edge | Claims | Explicitly does **not** claim |
|---|---|---|
| `calls` | The parser saw a call expression and resolved its callee | |
| `references` | Something holds a handle to this function: a dispatch-table entry, a callback field, a registration macro | That it is ever invoked. Enough to make deleting it unsafe, not enough to call it a call |
| `dispatches_to` | A base method points at an implementation that could answer for it | A proven override. No signature is compared |
| `framework_binds` | A framework wires these two symbols together: a pytest fixture, a Spring injection | A call. Nothing here is source a parser could have seen |
| `type_use` | A type is referenced in a constructor, method, delegate or record parameter | An import. Weighted below one |
| `co_changes` | These files keep changing in the same commit | Any code dependency at all |

The `references` distinction is not academic. A handler sitting in a dispatch
table is never called anywhere a parser can see. Counting that as "no use"
reported entire registration layers as safe to delete.

`framework_binds` is separated from `calls` for the same reason in reverse. A
fixture nobody calls and a collaborator nobody constructs are both genuinely
used, by the container. But an inferred wiring hop is not source, and letting it
render as a call would put it into an execution flow as though someone had
written it.

---

## Flows that say why they stopped

An execution flow walks the call graph from an entry point. Every walk ends, and
a trace that just stops reads identically whether execution really ends there or
the walker ran out of things it could follow.

So a flow never simply ends. It terminates with one of six reasons:

| Termination | Meaning |
|---|---|
| `no_callees` | No outgoing call edges recorded |
| `cycle` | Every successor was already on this trace: recursion or mutual calls |
| `depth_limit` | The hop budget ran out. Nothing is known beyond it |
| `confidence_filtered` | Every successor sat below the confidence floor |
| `excluded_target` | Every successor was a test, demo or fixture node |
| `callees_truncated` | Rows were cut before the walk saw them |

Two details carry most of the value. `no_callees` is **deliberately not called a
leaf**, because a symbol whose calls we failed to resolve looks exactly like a
function that genuinely calls nothing, and asserting the second is a claim the
graph cannot support. And when a confidence floor is what stopped the walk, the
flow reports *which origins it declined*, which is the part you can act on.

---

## What the graph powers

The graph is not the product. These are:

- **Blast radius.** Change a file, walk the dependency edges, get the set of
  things that can break. Confidence travels with it, so a speculative hop is
  visible as one.
- **Dead code.** Reachability over the union view, not over `calls` alone. A
  symbol reached only by a framework, a dispatch table or a type reference is
  not dead, and each of those is a different edge type for exactly this reason.
- **Communities.** Leiden clustering (Louvain as fallback) finds the modules
  your codebase actually has, which is frequently not the directory layout.
- **Centrality.** PageRank over the file tier ranks what everything depends on.
  Betweenness finds the bridges whose removal splits the graph. Neither is fed
  co-change edges, because "changes alongside many things" is not "many things
  depend on it".
- **Cycles.** Strongly connected components, which need their own documentation
  strategy because nothing in them can be explained before the others.
- **Execution flows.** Entry point to leaf, ranked, with the termination reason
  attached.
- **Framework wiring.** 22 detectors connect routes to handlers, DI
  registrations to implementations, and ORM entities to their relationships,
  across Django, FastAPI, Flask, Spring, ASP.NET, Rails, Laravel, Next.js,
  Express, Axum, Gin and more.

---

## Seeing it yourself

**For your agent**, via MCP:

| Tool | Gives you |
|---|---|
| `get_context(targets)` | Dependencies, dependents and co-change partners for a file or symbol |
| `get_risk(targets)` | What history and the graph say about touching these paths |
| `get_execution_flows()` | Traced flows with their termination reason |
| `get_dead_code()` | Unreachable files, unused exports and zombie packages, by confidence tier |
| `get_blast_radius()` | Cross-repo impact, in workspace mode |

**In the dashboard**, `repowise serve` gives you the graph, architecture,
coupling, blast-radius, knowledge-graph and dead-code views.

**Everything above is computed without a single model call.** An LLM is an
optional upgrade for prose quality in the wiki. It is never part of building the
graph, which is why the graph is reproducible and why indexing needs no API key.

---

## Honest ceilings

- **Resolution quality varies by language**, and the [language support
  page](LANGUAGE_SUPPORT.md) says how per language. Statically typed languages
  with explicit declarations resolve best. Dynamically typed and heavily
  reflective code resolves worst, and falls back to the low-confidence origins
  rather than pretending.
- **Receiver typing reads declarations with per-language patterns**, not a full
  type checker. A declaration the patterns cannot see is a receiver that does
  not get typed, and the call falls back to a weaker origin.
- **`dispatches_to` compares no signature.** It matches by method name, so it
  names a possible dispatch target rather than a proven override.
- **A repo-wide unique name is still a guess**, and `global_unique` at 0.50 is
  where that lives. It is kept because a labelled guess beats a missing edge for
  reachability, and it is labelled so nothing downstream can mistake it for a
  fact.
- **Method-level dead-code detection is not shipped**, for any language. It was
  measured, precision failed, and shipping it would have meant confidently
  recommending deletions that were wrong.
- **Roughly fifteen percent of our call edges are wrong.** Hand-graded from
  source, 270 rows across nine languages: 84.8% correct overall, and the misses
  concentrate in java, rust and cpp, which read 20/30, 22/30 and 23/30
  respectively. That figure is a floor rather than a best case, because the
  resolver changes made since the earliest rows were graded only remove wrong
  edges. Method, per-language and per-repository splits, and the same audit run
  against a competitor, are in the [graph-quality
  benchmark](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g1-edge-precision).
- **A compiler agrees with that figure, and adds the column we lose.** On Go and
  TypeScript the same edges were judged against the Go team's own RTA call graph
  and the `tsc` checker's own resolution, answer keys we neither wrote nor can
  tune. Precision there is 0.94 to 0.99 per cell and we are the most precise of
  three tools in all seven; recall is 0.32 to 0.96 and **we lead none of the Go
  cells**. Most of what we miss is interface dispatch, where the fan-out is 6.5
  distinct targets per call site and matching it would mean emitting edges we
  cannot stand behind. The [oracle-anchored
  cells](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g4-oracle-anchored)
  decompose the miss.

---

## See also

- [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) · what works per language, and the tier each one sits in
- [architecture/language-support.md](../architecture/language-support.md) · call resolution internals and the contributor recipe
- [architecture/graph-algorithms.md](../architecture/graph-algorithms.md) · PageRank, Leiden, betweenness and SCC in detail
- [DEAD_CODE.md](DEAD_CODE.md) · how reachability becomes a confidence-tiered report
- [CHANGE_RISK.md](CHANGE_RISK.md) · how the graph feeds a per-change risk score
- [reference/COMPUTED_GLOSSARY.md](../reference/COMPUTED_GLOSSARY.md) · every derived metric, defined
