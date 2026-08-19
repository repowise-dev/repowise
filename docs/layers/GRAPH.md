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
  <img src="https://img.shields.io/badge/compiler_graded-7_of_7_cells_undominated-DC2626?style=flat-square&labelColor=0A0A0A" alt="no tool is both more precise and more complete, in 7 of 7 compiler-graded cells" />
</p>

**Contents:** [The problem with a plain arrow](#the-problem-with-a-plain-arrow) ·
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

## Two stages, and they fail differently

An edge is two claims made by two different pieces of machinery, and the reason
to keep them apart is that they break in opposite directions.

**Stage one: capture.** Before anything can be resolved, the parser has to
notice that a call was written at all. That is a tree-sitter query per language,
listing the source shapes that count as a call site:

```scheme
; queries/go.scm -- Method call: obj.Method(args)
(call_expression
  function: (selector_expression
    operand: (identifier) @call.receiver
    field: (field_identifier) @call.target
  )
  arguments: (argument_list) @call.arguments
) @call.site
```

There is one of those files per language, and a shape that is not in it is a
call the graph will never contain. Go alone lists a plain call, a method call, a
package-qualified call, a chained call and a function passed as an argument, and
that last one is captured deliberately as a *reference* rather than a call,
because passing a handler is not invoking it.

A shape no query matches is invisible. Not low confidence, not unresolved:
**absent**. Nothing downstream can recover it, because nothing downstream knows
the call was there.

**Stage two: resolution.** Given a captured site, work out what the name points
at. `repo.save(draft)` hands you the name `save` and a receiver spelled `repo`,
and the job is to turn that into one declaration in one file. This is where the
29 origins below live, and it is the `user.save()` problem from the section
above.

| | fails when | costs you | how you find out |
|---|---|---|---|
| **capture** | nobody wrote a query for that call shape | **recall.** the edge does not exist | nothing internal can tell you, so it takes an outside answer key |
| **resolution** | the receiver cannot be typed, or the name is ambiguous | **precision** if it guesses, **recall** if it declines | the origin on the edge names the strategy that answered, so a wrong class is found and fixed once rather than per call site |

That asymmetry sets the whole design. A missed capture is silent, so it is
measured against a compiler rather than against ourselves. A bad resolution is
loud, so every edge is stamped with the strategy that produced it and nothing is
allowed to launder a guess into a fact.

**And at the bottom of the ladder repowise declines rather than guesses.** That
is a choice with a price, paid in the recall column below, and it is the right
way round: a missing arrow looks like missing information, a wrong arrow looks
like an answer.

---

## How good is it, and how we know

Two readings of the same question, and we graded only one of them.

### The one we did not grade

On Go the answer key is the **Go team's own RTA call graph** from
`golang.org/x/tools`, computed over the fully type-checked program. On TypeScript
it is the **`tsc` checker's own resolution** of every call site. We wrote
neither, we can tune neither, and anyone with the toolchain can regenerate both.

Seven cells, five repositories, **five tools**, 37,853 oracle edges. Of the call
edges we emit, the share the compiler confirms runs **0.943 to 0.992** per cell.

That number on its own is not the claim, because precision on its own has a cheap
way to win: draw one edge you are certain of and you score 1.000. Two of the five
tools do a version of exactly that. One scores 0.997 on cobra, the highest figure
in the whole experiment, from a graph holding **17% of the calls in the
repository**. Another takes both gitleaks cells from a graph that
finds 89% of the calls where we find 95%. Meanwhile the tool with the best recall emits, on the largest
repository measured, more than a third of its edges as calls the compiler says do
not exist.

**Recall alone is gameable in the other direction and precision alone in this
one, so the claim is the pair:**

> In all seven cells, **no tool that recovers as much of the call graph as we do
> gets more of it right.**

It names no threshold, so it cannot be tuned, and a new competitor can only break
it. Two were added after it was written and it held in all seven cells.

The weaker readings, so nobody has to infer them: most precise outright in one
cell, tied in one more, beaten in five by tools drawing much smaller graphs. And
against the two tools the experiment started with, most precise in seven of
seven, which is the narrower claim it should always be labelled as.

**Two languages, and only two.** C#, Java, Kotlin and C++ each need a toolchain
installed and a working build per repository, and nobody has done that here.
Python, Ruby and PHP can never have an oracle at all, because what a call
resolves to can change at runtime. That is a fact about those languages rather
than a gap in the harness, and it is why the hand-graded reading below is
permanent rather than a stopgap.

[The cells, the method and the graded pre-registration](../BENCHMARKS.md#8-the-same-question-against-an-answer-key-we-do-not-control)

### The one we did

Nine languages, 30 call edges per language per tool, every row opened in its own
file with its imports and enclosing scope, then the target declaration opened
too. **229 of 270 correct for us, 154 of 270 for CodeGraph 1.5.0**, intervals
disjoint. Four of the nine cells separate and five are ties, reported as ties.

Read our own number the other way round: **roughly fifteen percent of our call
edges are wrong**, concentrated in java, rust and cpp. That is the figure to plan
against, and it is a floor rather than a best case, because every resolver change
since the earliest rows were graded only removes wrong edges.

**The two readings agree.** On Go the hand grade says 96.7% for us and the
compiler says 97.6%, over roughly 1,600 edges rather than 30 rows. A person
reading source and a type checker landing within about a point of each other is
the strongest available evidence that the hand-graded half is accurate rather
than self-serving, and it is the result here we care about most.

[The nine cells, and all 540 graded rows with the reason each was given](../BENCHMARKS.md#7-edge-precision)

### The column we lose

Recall is the other half of the same question, and we do not lead it.

Across the five Go cells our recall runs 0.32 to 0.96 and **we lead none of
them**; codebase-memory-mcp leads four and CodeGraph the fifth. On cross-file
coverage over 35 repositories the same tool separates from us on 15 and we
separate on none. We do lead recall in both TypeScript cells, and we lead it over
the two tools that beat us on precision in every cell, which is the same trade
seen from the other side.

The oracle explains the trade rather than excusing it. **That tool recovers more
of the true call graph and emits far more that is not in it**: on the largest Go
repository measured, more than a third of what it emits is a call the compiler
says does not exist. Coverage rewards drawing edges and never asks whether they
are real, which is why no page in that benchmark prints a coverage number without
a precision number beside it.

Where our own miss actually goes, decomposed on one cell rather than waved at.
On syft without tests we miss **3,846 of the oracle's 7,898 edges**, and
**44% of that miss is dynamic dispatch alone, with a further 39% dispatch with a
closure at one end**. The two buckets overlap, so neither is the whole gap. Interface dispatch is the ceiling and
nobody in the comparison has cleared it: of 3,303 dispatch edges we match 12,
CodeGraph 35, codebase-memory-mcp 81, at 6.5 distinct possible targets per call
site. Matching that recall means emitting six edges where one is right, which is
the behaviour the precision table charges the other tool for.

The obvious cheap fix was priced and refused: giving Go `func` literals a symbol
would recover **50** static edges on that cell, not the 1,309 the raw closure
count suggests, because the rest need the dispatch ceiling cleared first.

[Both tables, the recall decomposition and what it would cost to close](https://github.com/repowise-dev/repowise-bench/tree/master/graph/experiments/g4-oracle-anchored)

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
- **Roughly fifteen percent of our call edges are wrong**, and **we lead no Go
  recall cell**. Both are measured, both are above under [how good is it, and how
  we know](#how-good-is-it-and-how-we-know), and neither is buried down here.
- **Two competing tools are more precise than us in five of seven oracle cells.**
  Each of them draws a much smaller graph, which is the whole reason, and it is
  stated above rather than left out. A precision figure quoted without the recall
  beside it is a misuse of this data, including by us.
- **The compiler-graded reading covers two languages.** Go and TypeScript are the
  only ones with an oracle, so on the other seventeen the precision figure is the
  hand-graded one, at 30 rows per language. That is a smaller n and a method we
  ran ourselves; the two agree to within a point where both exist, which is the
  reason to trust the half where only one does.
- **A competitor's coverage lead is only priced on two languages too.** The tool
  that beats us on cross-file coverage across 35 repositories has an
  oracle-anchored precision figure on go and typescript alone. That its extra
  edges are mostly wrong is measured there and inferred elsewhere, and the
  benchmark says so rather than generalising quietly.

---

## See also

- [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) · what works per language, and the tier each one sits in
- [architecture/language-support.md](../architecture/language-support.md) · call resolution internals and the contributor recipe
- [architecture/graph-algorithms.md](../architecture/graph-algorithms.md) · PageRank, Leiden, betweenness and SCC in detail
- [DEAD_CODE.md](DEAD_CODE.md) · how reachability becomes a confidence-tiered report
- [CHANGE_RISK.md](CHANGE_RISK.md) · how the graph feeds a per-change risk score
- [reference/COMPUTED_GLOSSARY.md](../reference/COMPUTED_GLOSSARY.md) · every derived metric, defined
- [BENCHMARKS.md §7](../BENCHMARKS.md#7-edge-precision) and [§8](../BENCHMARKS.md#8-the-same-question-against-an-answer-key-we-do-not-control) · the precision numbers on this page, with their sample sizes and intervals
- [repowise-bench/graph](https://github.com/repowise-dev/repowise-bench/tree/master/graph) · the harnesses, the five arms, the graded rows and the pre-registrations behind all of it
