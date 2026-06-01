# The Five Intelligence Layers

repowise indexes your codebase **once**, builds five intelligence layers, then
keeps them in sync on every commit. This document is the deep dive — the README
gives the one-paragraph version of each layer and links here for the detail.

1. [Graph Intelligence](#graph-intelligence)
2. [Git Intelligence](#git-intelligence)
3. [Documentation Intelligence](#documentation-intelligence)
4. [Decision Intelligence](#decision-intelligence)
5. [Code Health Intelligence](#code-health-intelligence)

Two cross-cutting capabilities sit on top of the layers:

- [Proactive context enrichment — hooks](#proactive-context-enrichment--hooks)
- [Auto-sync — five ways to stay current](#auto-sync--five-ways-to-stay-current)
- [Auto-generated CLAUDE.md](#auto-generated-claudemd)

---

## Graph Intelligence

tree-sitter parses every file across 15 languages into a **two-tier dependency
graph** — file nodes and symbol nodes (functions, classes, methods). A 3-tier
call resolver with confidence scoring handles import aliases, barrel
re-exports, and namespace imports. Heritage extraction covers `extends`,
`implements`, trait impls, derive macros, mixins, and extension conformance.

- **Leiden community detection** finds logical modules even when your directory
  structure doesn't reflect them.
- **PageRank, betweenness centrality, SCC analysis, and execution-flow tracing**
  from entry points identify your most central, most coupled, and most
  traversed code.
- **Framework-aware edges** connect routes to handlers for Django, FastAPI,
  Flask, ASP.NET, Spring Boot, Express/NestJS, Gin/Echo/Chi, Axum/Actix, Rails,
  Laravel, and more.

See [`docs/LANGUAGE_SUPPORT.md`](LANGUAGE_SUPPORT.md) for per-language coverage
and [`docs/COMPUTED_GLOSSARY.md`](COMPUTED_GLOSSARY.md) for every derived metric.

---

## Git Intelligence

repowise mines your git history (per-file, configurable depth) to produce
signals no static analysis can find.

**Hotspots** — files in the top 25% of *both* churn and complexity. These are
where bugs live. Flagged in the dashboard, in `CLAUDE.md`, and surfaced by
`get_risk()` before your agent touches them.

**Ownership** — `git blame` aggregated into ownership percentages per author.
Know who to ping. Know where knowledge silos exist.

**Co-change pairs** — files that change together in the same commit *without* an
import link. Hidden coupling that AST parsing cannot detect. `get_context()`
surfaces co-change partners alongside direct dependencies.

**Bus factor** — files owned >80% by a single author. Shown in the ownership
view, surfaced in `CLAUDE.md` as knowledge risk.

**Significant commits** — the last 10 meaningful commit messages per file
(filtered: no merges, no dependency bumps, no lint) feed generation prompts, so
the wiki explains *why* code is structured the way it is.

**Contributor profiles** — every author with commits gets a profile page:
modules they own, top files, co-authors, commit category mix
(feat / fix / refactor / docs / test / chore / perf), silo modules they're
solely on, bus-factor risk files, and dead-code burden. Surfaced via
`/repos/<id>/owners` and linked from every owner reference.

**Module health** — a composite 0–100 score per top-level module derived from
silo penalty, hotspot density, dead-code percentage, average churn, doc
coverage, and median bus factor. Surfaced on the Risk page and the per-module
detail page, with cross-links to owners, hotspots, and governing decisions.

**Reviewer suggestions** — paste a PR file list into Blast Radius and get a
ranked list of likely reviewers, scored by direct authorship (×1.0), co-change
partners (×0.5), and recency (×0.4), capped at the 5 strongest co-change signals
per file.

---

## Documentation Intelligence

An LLM-generated wiki for every module and file, rebuilt **incrementally** on
every commit.

- **Coverage tracking** — what's documented and what isn't.
- **Freshness scoring** per page — confidence scores show how current each page
  is relative to the underlying code.
- **Semantic search via RAG** — hybrid retrieval (full-text + vector merged via
  Reciprocal Rank Fusion) with PageRank bias and 1-hop graph expansion.

A typical single-commit update touches 3–10 pages and completes in under 30
seconds — only the pages your change actually touched are regenerated.

---

## Decision Intelligence

**The layer nobody else has.** Architectural decisions mined from **eight
sources** — ADR files (Nygard/MADR), CHANGELOG entries, PR and squash-commit
bodies, inline markers, git archaeology, README/docs, centrality-bounded code
comments, and the LLM doc-generation pass itself — linked to the graph nodes
they govern and tracked for staleness as code evolves.

```python
# WHY: JWT chosen over sessions — API must be stateless for k8s horizontal scaling
# DECISION: All external API calls wrapped in CircuitBreaker after payment provider outages
# TRADEOFF: Accepted eventual consistency in preferences for write throughput
```

Every decision is **evidence-backed**: each rationale traces to a verbatim
source span (ADR quote, commit body, code comment), and an anti-hallucination
substring gate stamps each as **verified / fuzzy / unverified** — corroborating
sources raise confidence rather than overwrite each other.

Decisions form a **graph**: typed edges (`supersedes` / `refines` /
`relates_to` / `conflicts_with`) let `get_why()` answer *"why is auth structured
this way?"* with a lineage chain (sessions → JWT → OAuth2), auto-detect when a
new commit reverses an old decision, and flag two active decisions that
contradict each other.

These structured records surface everywhere your agent already looks —
`get_why()` for the full archaeology, governing decisions in `get_context()`, a
`governance_risk` flag in `get_risk()` PR review, a Key Decisions section in
`get_overview()`, and `ungoverned_hotspot` / `stale_governance` /
`contradictory_decision` findings in the code-health layer.

```bash
repowise decision add              # guided interactive capture (~90 seconds)
repowise decision confirm          # review auto-proposed decisions from git history
repowise decision health           # stale, conflicting, ungoverned hotspots
```

```
repowise decision health

  2 stale decisions
    → "JWT over sessions" — auth/service.ts rewritten 3 months ago, decision may be outdated
    → "EventBus in-process only" — 8 of 14 governed files changed since recorded

  1 conflict
    → payments/: two decisions with overlapping scope and contradictory rationale

  1 ungoverned hotspot
    → payments/processor.ts — 47 commits/month, no architectural decisions recorded
```

The "why" usually walks out the door — when a teammate leaves, or when you
reopen your own repo six months later. Decision intelligence keeps it in the
codebase.

---

## Code Health Intelligence

repowise computes a **1–10 health score for every file** from **25 deterministic
biomarkers** — McCabe complexity, deep nesting, brain methods, class cohesion
(LCOM4), god classes, native Rabin–Karp clone detection, untested hotspots,
function-level churn, code-age volatility, ownership dispersion, change entropy,
co-change scatter, prior-defect history, test-quality smells, and more.

**Zero LLM calls. Zero cloud requirement. Zero new runtime dependencies** —
pure Python over tree-sitter and git data, designed to finish in under 30
seconds on a 3,000-file repo.

The biomarker **weights are calibrated offline against a real defect corpus, not
hand-tuned**: each file is scored at the pre-window commit (T0, no leakage) and
an L2-logistic regression — with NLOC as an explicit control — fits each
biomarker's defect lift *beyond* file size. Only the learned constants ship; the
runtime stays fully deterministic.

```bash
repowise health                       # KPIs + lowest-scoring files
repowise health --coverage cov.lcov   # ingest coverage, light up untested-hotspot
repowise health --refactoring-targets # ranked by impact / effort
repowise health --trend               # last 10 snapshots + declining/predicted-decline alerts
repowise status                       # one-line summary in the status report
```

- **Coverage ingestion** — LCOV, Cobertura, Clover, or normalized JSON light up
  the test-coverage biomarkers (`untested_hotspot`, `coverage_gap`,
  `coverage_gradient`).
- **Trend tracking** — a rolling 50-row snapshot history powers `Declining
  Health` and `Predicted Decline` alerts.
- **Refactoring suggestions** — deterministic, rule-based, ranked by
  impact / effort, on the dashboard and via `get_health(include=["refactoring"])`.
- **Per-file overrides** via `.repowise/health-rules.json`.

Validated against real defect history — see
[`docs/CODE_HEALTH.md`](CODE_HEALTH.md) for the full user guide, the per-biomarker
reference, and the calibration story, and
[repowise-bench](https://github.com/repowise-dev/repowise-bench) for the
reproducible defect-prediction and head-to-head benchmarks.

---

## Proactive context enrichment — hooks

Most MCP tools are passive — the agent has to know to call them. repowise hooks
are **active**. They inject graph context into every search automatically, so
agents are smarter even when they don't explicitly ask for help. Hooks are
installed automatically during `repowise init`.

### PreToolUse — every search gets graph context

When your AI agent runs `Grep` or `Glob`, repowise intercepts the call and
enriches it with the top 3 related files — found via multi-signal search (symbol
name match, file-path match, full-text search on wiki content), ranked by
relevance then PageRank. No LLM calls. No network. Pure local SQLite queries.

```
[repowise] 3 related file(s) found:

  src/core/ingestion/graph.py
    Symbols: class:GraphBuilder, method:__init__, method:build
    Imported by: src/core/ingestion/__init__.py
    Uses: src/core/analysis/communities.py, src/core/analysis/execution_flows.py
```

### PostToolUse — auto-detect stale wiki

After a successful `git commit`, repowise checks whether the wiki is out of date
and notifies the agent:

```
[repowise] Wiki is stale — last indexed at commit a1b2c3d4, HEAD is now f9a0499b.
Run `repowise update` to refresh documentation and graph context.
```

---

## Auto-sync — five ways to stay current

repowise keeps your intelligence layers in sync with your code. Pick the method
that fits your workflow:

| Method | Command | Best for |
|--------|---------|----------|
| **Post-commit hook** | `repowise hook install` | Set-and-forget local development |
| **File watcher** | `repowise watch` | Active development without committing |
| **GitHub webhook** | Configure in repo settings | Teams, CI/CD |
| **GitLab webhook** | Configure in project settings | Teams, CI/CD |
| **Polling fallback** | Automatic with `repowise serve` | Safety net for missed webhooks |

```bash
repowise hook install             # install post-commit hook (current repo)
repowise hook install --workspace # install for all workspace repos
repowise hook status              # check if hooks are installed
repowise watch                    # or use the file watcher (single repo)
repowise watch --workspace        # all workspace repos
```

A typical single-commit update touches 3–10 pages and completes in under 30
seconds. Full guide: [`docs/AUTO_SYNC.md`](AUTO_SYNC.md).

---

## Auto-generated CLAUDE.md

After every `repowise init` and `repowise update`, repowise regenerates your
`CLAUDE.md` from actual codebase intelligence — not a template. No LLM calls.
Under 5 seconds.

```bash
repowise generate-claude-md
```

The generated section includes: architecture summary, module map, hotspot
warnings, ownership map, hidden coupling pairs, active architectural decisions,
and dead-code candidates. A user-owned section at the top is never touched.

```markdown
<!-- REPOWISE:START — managed automatically, do not edit -->
## Architecture
Monorepo with 4 packages. Entry points: api/server.ts, cli/index.ts.

## Hotspots — handle with care
- payments/processor.ts — 47 commits/month, high complexity, primary owner: @sarah
- shared/events/EventBus.ts — 23 dependents, co-changes with all service listeners

## Active architectural decisions
- JWT over sessions (auth/service.ts) — stateless required for k8s horizontal scaling
- CircuitBreaker on all external calls — after payment provider outages in Q3 2024

## Hidden coupling (no import link, but change together)
- auth.ts ↔ middleware/session.ts — co-changed 31 times
<!-- REPOWISE:END -->
```

---

## Dead code detection

Pure graph traversal and SQL. No LLM calls. Completes in under 10 seconds for
any repo size.

```
repowise dead-code

  23 findings · 4 safe to delete

  ✓ utils/legacy_parser.ts          file      1.00   safe to delete
  ✓ auth/session.ts                 file      0.92   safe to delete
  ✓ helpers/formatDate              export    0.71   safe to delete
  ✗ analytics/v1/tracker.ts         file      0.41   recent activity — review first
```

Conservative by design. `safe_to_delete` requires confidence ≥ 0.70 and excludes
dynamically-loaded patterns (`*Plugin`, `*Handler`, `*Adapter`, `*Middleware`).
Dynamic-import detection (`importlib.import_module()`, `__import__()`) and
framework awareness (Flask/FastAPI/Django/Rails/Laravel/TYPO3 routes and
convention files) further reduce false positives. repowise surfaces candidates.
Engineers decide.
