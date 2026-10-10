# The Intelligence Layers

Repowise builds one local index with five layers. Everything it does, from dead code
to change risk to the answers your agent gets, is one of those layers or a
combination of them. This page is the map: what each layer holds, what is built on
it, and which page has the detail.

```
  What you use     answers · change risk · Fix first · dead code · impacted tests
                   refactoring plans · doc drift · contracts across repositories
                                             ▲
  Five layers      1 Graph · 2 Git history · 3 Docs · 4 Decisions · 5 Health
                                             ▲
  One index        your source, its git history, your markdown, your coverage
```

`repowise init` parses the code and reads the history once. `repowise update` keeps
it current after each commit, re-analysing only what changed. Almost everything is
computed without a model: a provider key adds written prose to the docs layer and a
few optional extras, and never switches a layer on.

## 1. Graph: what is connected to what

Every file, symbol, import and call in the repository, parsed from an AST in 27
languages. Each call edge is stamped with how it was resolved (one of 39 named
resolution origins) and how far to trust it, across 17 edge types. Communities,
centrality, cycles and execution flows from each entry point are computed on top.

The graph is what lets every other layer point at the right code: git history
attaches to files the graph knows, health scores what the graph parsed, tests reach
code through graph edges.

| Built on it | What you get | Read more |
|---|---|---|
| Call graph and execution flows | Callers, callees, dependency paths and traced flows, each edge with a confidence | [GRAPH.md](GRAPH.md) |
| Dead code | Unreachable files, unused exports, unused packages, by confidence tier | [DEAD_CODE.md](DEAD_CODE.md) |
| Inferred test links | Which tests reach a file, without a coverage report | [TEST_INTELLIGENCE.md](TEST_INTELLIGENCE.md) |
| Cross-repo contracts | HTTP, gRPC, topic and OpenAPI contracts matched between repositories, with breaking-change detection | [WORKSPACES.md](../scale/WORKSPACES.md) |
| Language coverage | What each of the 41 supported languages gets | [LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) |

## 2. Git history: where change and breakage concentrate

Churn, hotspots, co-change pairs, ownership, bus factor and bug-fix commits, read from
the repository's own history. Static analysis cannot see any of this: two files with
no import between them can still change together every week.

| Built on it | What you get | Read more |
|---|---|---|
| Ownership and knowledge risk | Owners, bus factor, silos, knowledge loss when a main author goes quiet, suggested reviewers | [OWNERSHIP.md](OWNERSHIP.md) |
| Bug history | Fix commits traced to files and symbols, bug magnets | [BUG_HISTORY.md](BUG_HISTORY.md) |
| Hidden coupling | Files that change together with no import link | [CHANGE_RISK.md](CHANGE_RISK.md) |
| Branch overlap | Other branches editing the same files or their co-change partners | [CHANGE_RISK.md](CHANGE_RISK.md#branch-overlap) |
| Secrets in history | Keys and tokens anywhere in the full history, not only the working tree | [SECURITY.md](SECURITY.md) |

## 3. Docs: the codebase, readable and searchable

A wiki page for every module and file, rendered from the code's structure with no
key, or written by a model when you choose. Full-text, semantic, symbol and path
search over code and pages. Pages carry freshness and confidence, and rebuild
incrementally.

| Built on it | What you get | Read more |
|---|---|---|
| Generated wiki | Module, file, layer, API and onboarding pages, with diagrams | [WIKI.md](WIKI.md) |
| Search and cited answers | `get_answer`, `search_codebase`, `repowise ask` | [WIKI.md](WIKI.md) |
| Doc drift | Your own markdown checked against the tree: every reference to code that is gone | [DOC_DRIFT.md](DOC_DRIFT.md) |
| Agent instructions | A managed `CLAUDE.md` and `AGENTS.md` regenerated from the index | [editor-files.md](../architecture/editor-files.md) |

## 4. Decisions: why the code is shaped this way

Architectural decisions found in ADR files, `# WHY:` and `# DECISION:` comments,
commit and PR history, and your own agent sessions. Each decision is tied to the code
it governs and to the evidence it came from, and it is flagged when the code moves
away from it.

| Built on it | What you get | Read more |
|---|---|---|
| Decision records and lifecycle | Candidates, accepted, superseded and stale decisions, with evidence spans | [DECISIONS.md](DECISIONS.md) |
| `get_why` | The governing decision for a file, or git archaeology when none exists | [DECISIONS.md](DECISIONS.md) |
| Ungoverned hotspots | Heavily changed files no decision explains | [DECISIONS.md](DECISIONS.md) |

## 5. Health and change intelligence: what to fix, and what a change will do

53 deterministic detectors score every file 1 to 10 across defect risk,
maintainability and performance. 25 of them may move the defect score, calibrated
against real bug history. The same machinery judges a change before it merges.

| Built on it | What you get | Read more |
|---|---|---|
| Code health score | Per-file score, bands, trends, badges | [CODE_HEALTH.md](CODE_HEALTH.md) |
| Fix first | A ranked queue of what to fix, by impact and effort (`repowise next`) | [CODE_HEALTH.md](CODE_HEALTH.md#fix-first) |
| Performance findings | N+1 queries, I/O in loops, blocking calls in async code, traced across functions | [CODE_HEALTH.md](CODE_HEALTH.md#performance-findings) |
| Refactoring plans | Extract Class, Extract Method, Extract Helper, Move Method, Break Cycle, Split File, Performance Fix | [REFACTORING.md](REFACTORING.md) |
| Change risk | Where a commit or range ranks against the repo's recent history, and what it made worse | [CHANGE_RISK.md](CHANGE_RISK.md) |
| Tests and coverage gates | Impacted tests, patch coverage, untested hotspots | [TEST_INTELLIGENCE.md](TEST_INTELLIGENCE.md), [CI.md](../start/CI.md) |
| Security signals | Secrets and risky calls, with a pre-commit check and a CI gate | [SECURITY.md](SECURITY.md) |

## Where the layers meet

The most useful answers cross layers. A few examples:

**Untested hotspot (graph + git + health).** The `untested_hotspot` detector fires on
a file that churns heavily in git, is central in the graph (four or more dependents,
or top-decile activity), and is under-tested: low line coverage where you ingested a
report, or no test reaching it through the graph where you did not. It can lead the
Fix first queue.

**What breaks if I change this (graph + git + health).**
`get_risk(changed_files=[...])` reads dependents from the graph, the files that
usually change alongside these from git, and the tests that reach the change. It
leads with a directive: what may break, which co-changes look missing, which tests to
run. Each test row says whether its basis is measured or inferred.

**A warning at edit time (git + agent hooks).** When an agent touches a file with a recent
run of bug fixes, a hook adds one line to its context: how many fixes, how recent,
which function they cluster in. The generated `CLAUDE.md` lists files that need care
in the same order.

**Missing rationale (decisions + git + health).** A hotspot no recorded decision
governs shows up in `repowise decision health`, in `get_why`, and as an
`ungoverned_hotspot` finding in code health.

**Across repositories (graph + git, per repo).** In a workspace, contract links from
the graph and co-change from git combine into cross-repo blast radius, consumer test
impact and architecture rules you can gate in CI. See
[WORKSPACES.md](../scale/WORKSPACES.md).

## Reaching the layers

| Question | CLI | MCP tool |
|---|---|---|
| What is this repository? | `repowise serve` (dashboard) | `get_overview` |
| How does X work, where is Y? | `repowise ask`, `repowise search` | `get_answer`, `search_codebase` |
| What is this file or symbol, who calls it? | `repowise context`, `repowise symbol` | `get_context`, `get_symbol` |
| What breaks if I change it? | `repowise risk` | `get_risk`, `get_change_risk` |
| Why is it built this way? | `repowise why` | `get_why` |
| What should we fix first? | `repowise next`, `repowise health` | `get_health` |
| What can we delete? | `repowise dead-code` | `get_dead_code` |
| Which tests matter? | `repowise impacted-tests` | `get_risk(changed_files=[...])` |

18 MCP tools are registered; a single-repo server exposes 10 by default and the rest
are opt-in. See [MCP_TOOLS.md](../agent/MCP_TOOLS.md) and the
[CLI reference](../reference/CLI_REFERENCE.md). Every derived metric is defined in
the [computed glossary](../reference/COMPUTED_GLOSSARY.md).

## Delivery to agents

Most MCP tools wait to be called. Two mechanisms put the layers in front of the agent
without that:

- **Hooks.** `repowise init` installs agent hooks for Claude Code (and Codex with
  `--codex`). They fire on session start, searches, reads, edits and failed paths, and
  add a line only when there is evidence the agent needs it: a governing decision, a
  bug-magnet file, a stale read, a search that found nothing. No model calls, no
  network. See [HOOKS.md](../agent/HOOKS.md).
- **Generated `CLAUDE.md` and `AGENTS.md`.** `init` and `update` regenerate a managed
  section from the index: architecture summary, key modules, entry points, files that
  need care, code health, standing decisions and your build commands. Anything you
  write outside the markers is kept.

[Distill](../agent/DISTILL.md) uses the same index to compress noisy command output
before the agent reads it.

## Freshness

`repowise init` installs a post-commit hook by default, so every commit runs an
incremental `repowise update` in the background. A file watcher, GitHub and GitLab
webhooks, and a polling fallback under `repowise serve` cover other setups. When the
index falls behind, the session-start hook says so and MCP responses carry a
`stale_warning`. See [AUTO_SYNC.md](../scale/AUTO_SYNC.md).
