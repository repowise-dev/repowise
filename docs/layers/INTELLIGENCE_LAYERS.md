# The Intelligence Layers

Repowise builds one local index with five layers. Everything it does, from dead code
to change risk to the answers your agent gets, is one of those layers or a
combination of them. This page is the map: what each layer is, how they combine,
and which page has the detail.

```
  What you use     answers, change risk, Fix first, dead code, impacted tests,
                   refactoring plans, doc drift, contracts across repositories
                                             ^
  Five layers      1 Graph, 2 Git history, 3 Docs, 4 Decisions, 5 Health
                                             ^
  One index        your source, its git history, your markdown, your coverage
```

`repowise init` parses the code and reads the history once. `repowise update` keeps
it current after each commit, re-analysing only what changed. Almost everything is
computed without a model: a provider key adds written prose to the docs layer and a
few optional extras, and never switches a layer on.

## The five layers

**1. Graph: what is connected to what.** Every file, symbol, import and call, parsed
to an AST in 29 languages (43 on the support ladder). Each call edge says how it was
resolved (one of 40 resolution origins) across 18 edge types, with communities,
centrality, cycles and execution flows on top. Dead code, inferred test links and
cross-repo contracts are built on it. Read [GRAPH.md](GRAPH.md),
[LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md), [DEAD_CODE.md](DEAD_CODE.md).

**2. Git history: where change and breakage concentrate.** Churn, hotspots,
co-change, ownership, bus factor and bug-fix commits from the repository's own
history, which static analysis cannot see. Ownership, bug history, hidden coupling,
secrets in history, Change Records and AI attribution are built on it. Read
[OWNERSHIP.md](OWNERSHIP.md), [BUG_HISTORY.md](BUG_HISTORY.md),
[CHANGES.md](CHANGES.md), [ATTRIBUTION.md](ATTRIBUTION.md).

**3. Docs: the codebase, readable and searchable.** A wiki page for every module and
file, built from code with no key or written by a model, with full-text, semantic,
symbol and path search and per-page freshness. Cited answers, doc drift checks and
the generated `CLAUDE.md` and `AGENTS.md` come from it. Read [WIKI.md](WIKI.md),
[DOC_DRIFT.md](DOC_DRIFT.md).

**4. Decisions: why the code is shaped this way.** Architectural decisions from ADR
files, `# WHY:` and `# DECISION:` comments, commit and PR history and agent sessions,
each tied to the code it governs and flagged when the code moves away from it. Read
[DECISIONS.md](DECISIONS.md).

**5. Health: what to fix, and what a change will do.** 59 deterministic detectors
score every file across defect risk, maintainability and performance; 22 of them
move the health score, calibrated against real bug history. The Fix first queue,
performance findings, refactoring plans, change risk, test intelligence and security
signals are built on it. Read [CODE_HEALTH.md](CODE_HEALTH.md),
[PERFORMANCE.md](PERFORMANCE.md), [REFACTORING.md](REFACTORING.md),
[CHANGE_RISK.md](CHANGE_RISK.md), [TEST_INTELLIGENCE.md](TEST_INTELLIGENCE.md),
[SECURITY.md](SECURITY.md), [RUNTIME_EVIDENCE.md](RUNTIME_EVIDENCE.md).

**Where the layers roll up.** A Change Record is one merged change with evidence from
every layer. A component is a part of the codebase that health, hotspots, bus factor,
test reach and dead code roll up to. Change policy and evidence reports are built on
both: [CHANGES.md](CHANGES.md), [REPORTS.md](REPORTS.md).

## Where the layers meet

The most useful answers cross layers:

- **Untested hotspot (graph + git + health).** A file that churns heavily, is central
  in the graph, and is under-tested (low ingested coverage, or no test reaching it
  through the graph) can lead the Fix first queue.
- **What breaks if I change this (graph + git + health).**
  `get_risk(changed_files=[...])` reads dependents from the graph, usual co-changes
  from git, and the tests that reach the change, and leads with what may break and
  which tests to run.
- **A warning at edit time (git + agent hooks).** When an agent touches a file with a
  recent run of bug fixes, a hook adds one line to its context: how many fixes, how
  recent, which function they cluster in.
- **Missing rationale (decisions + git + health).** A hotspot no recorded decision
  governs shows up in `repowise decision health`, in `get_why`, and as an
  `ungoverned_hotspot` finding in code health.
- **Across repositories (graph + git, per repo).** In a workspace, contract links and
  co-change combine into cross-repo blast radius and consumer test impact. See
  [WORKSPACES.md](../scale/WORKSPACES.md).

## Reaching the layers

Your agent reaches every layer through the MCP tools
([MCP_TOOLS.md](../agent/MCP_TOOLS.md)) and hooks that add context without being
asked ([HOOKS.md](../agent/HOOKS.md)); you reach them through the
[CLI](../reference/CLI_REFERENCE.md) and the [dashboard](../start/DASHBOARD.md).
A post-commit hook keeps the index current ([AUTO_SYNC.md](../scale/AUTO_SYNC.md)).
Every derived metric is defined in the
[computed glossary](../reference/COMPUTED_GLOSSARY.md).
