# Changelog

All notable changes to repowise will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Use `git-cliff` to auto-generate entries from conventional commits -->

---

## [Unreleased]

### Changed
- **Consolidated the MCP tool surface.** Removed six redundant MCP tools (`annotate_file`, `get_callers_callees`, `get_community`, `get_graph_metrics`, `get_architecture_diagram`, `update_decision_records`) whose capabilities are already covered by `get_context(include=[...])` and `get_why`. The MCP server exposes 13 tools: 10 in single-repo mode plus three workspace-only tools (`get_blast_radius`, `get_conformance`, `get_architecture`). Documentation and tool counts across the project were reconciled to match.

---

## [0.20.0] — 2026-06-16

### Added
- **Churn x complexity quadrant on the hotspots tab.** The hotspots view now plots files on a churn-versus-complexity quadrant, making it easy to spot the high-churn, high-complexity files that warrant attention first. (#491)
- **Per-file process, people, and topology signals.** The file page now surfaces per-file process signals (how the file changes over time), people signals (ownership and contributor spread), and topology signals (how connected the file is), each already computed during indexing. (#490)
- **Per-file health score over time.** The file page, the file drawer, and the MCP `get_health` surface now show a file's health score history, so you can see whether a file is trending better or worse. (#489)
- **Health bands, repo distribution, and a README badge.** Code health is now bucketed into named bands with a repo-wide distribution view, and a health badge can be embedded in your README. (#485)

### Changed
- **Quieter, polished `update` CLI UX.** `repowise update` now uses the same calm, panel-based progress output as `init`, with a `-v` flag for verbose detail. The previously monolithic update command was also split into a package for maintainability. (#476, #477)
- **Co-change page reframed as a temporal hint.** The cross-repo co-change page now presents its data as a temporal hint rather than an authoritative dependency, and the average-strength figure is displayed correctly. (#481)

### Fixed
- **Workspace job progress stays accurate.** In multi-repo workspace mode, job listing and progress now read from the correct per-repo database, stale jobs left running after a server restart are reset, and the progress timer and phase labels reflect persisted state instead of component mount time. (#487)
- **Co-change noise filtering and cross-repo strength normalization.** Noise files are filtered out of co-change analysis and cross-repo co-change strength is normalized so the signal is comparable across repos. (#480)
- **Like-with-like population comparison in coordinator health.** Coordinator health now compares files against like-sized populations rather than mixing dissimilar groups. (#479)

### Documentation
- **README dual-audience positioning.** The README was reworked for a dual-audience frame and now surfaces change-risk, agent provenance, and wiki styles. (#478)
- Plugin: version bump to 0.20.0 (no command/skill/hook/MCP-surface changes).

### Dependencies
- Bumped `pyjwt` from 2.12.1 to 2.13.0, a security release bundling five advisory fixes. (#488)

---

## [0.19.1] — 2026-06-13

### Fixed
- **`repowise serve` web UI failed to build for the release tarball.** The bundled web dashboard could not be compiled once a workspace-package barrel entry was imported as a value (introduced by the wiki-styles constants), because Webpack could not resolve the ESM `.js` re-export specifiers in `@repowise-dev/types` / `@repowise-dev/ui` back to their `.ts` sources. Added an extension alias to the Next.js build so `.js` specifiers map to `.ts`/`.tsx`. This affected only the published `repowise-web.tar.gz`; the Python wheel was unaffected. (#471)

---

## [0.19.0] — 2026-06-13

### Added
- **Wiki styles: selectable documentation voice.** Generated pages can now be produced in one of four styles: `comprehensive` (the default, unchanged), `caveman` (token-condensed, AI-first), `reference` (API-manual), or `tutorial` (beginner-friendly), plus user-defined custom styles. Styles change only the prose voice and density, not the document structure: headings, sections, table of contents, search, and cross-links are unaffected. (#468)
- **OpenCode CLI provider.** A new local OpenCode LLM provider runs documentation generation through the local OpenCode CLI via `opencode run --format json`. Uses `asyncio.create_subprocess_exec` (no shell), parses JSONL output, validates model names against a safe character set, and treats `opencode/*` cost as `$0.00`. No API keys are stored; OpenCode manages its own auth and model selection through its provider system. Interactive selection detects the OpenCode CLI on `PATH` and shows helpful install/setup instructions when it's missing. (#436)
- **Health score self-validation.** The code-health surface now validates the score against each repo's own bug history: it ranks files by health, takes the 20 lowest, and reports how many were touched by a `fix:` commit in the trailing ~6 months versus the repo-wide baseline rate (the lift), e.g. "16/20 lowest-health files had a bug fix in the last 6 months, 3.3x the 24% baseline". Stays silent when there is too little history to be honest. (#438)
- **Error-handling maintainability biomarker.** A new biomarker surfaces swallowed-exception and unsafe-unwrap anti-patterns as a bounded maintainability finding: empty or trivial `catch`/`except` bodies across Python, JS/TS, Java, Kotlin, C#, and C++, plus Python catch-all `except:` / `except Exception:` / `except BaseException:`. (#453)
- **MCP streamable HTTP transport.** The MCP server can now serve over a streamable HTTP transport in addition to stdio. (#444)
- **`REPOWISE_PORT` env var.** `repowise serve` now honours the `REPOWISE_PORT` environment variable for the server port. (#455)

### Changed
- **Web dashboard UX overhaul.** End-to-end rework of the web UI: a slimmer six-group sidebar (Overview, Docs, Architecture, Code Health, People and History, Chat) shared across desktop and mobile, Overview as the repo landing page, canonical entity pages, a single unified architecture destination, and surfacing of git/agent provenance data that was already persisted but previously invisible. Theme unchanged; design tokens were only added, never renamed. Retired pages (hotspots, ownership, dead-code, blast-radius) and stub routes redirect into their new homes, so every old URL still resolves. (#466)
- **Hybrid MCP improvements.** A batch of MCP server improvements centered on making a tool response trustworthy enough that the agent never re-reads the source it just paid for: a verified trust contract, an honest savings ledger, indexing of module-level constants, and trimmed per-call token overhead. Net additive to the tool surface, with no breaking changes to existing tool contracts. (#467)
- **Change-risk clarity.** Change-risk now prioritises repo-relative signals and uses honest driver labels instead of misleading absolute framing. (#465, #469)
- **Dead-code framing.** Findings are now framed as cleanup candidates rather than safe-to-delete, reflecting that static reachability can't prove a symbol is unused. (#433)

### Fixed
- **Co-change strength display.** Co-change strength now shows the raw score instead of a misleading percentage. (#439)
- **Health scoring of module-level JS callbacks.** Module-level JavaScript callbacks are now scored correctly. (#456)
- **Health trend wording.** Clarified how health-trend score changes are presented in the web UI. (#457)
- **CLI model selection.** The CLI now honours the `config.yaml` model when a provider is set via env var or flag. (#442)
- **Chat config inheritance.** Chat now inherits the per-repo provider, model, and key from the init config. (#434)

### Performance
- **XL-repo indexing pass.** Faster indexing on very large repos via cpp hint regex tuning, git deep-walk improvements, and XAML index reuse. (#459)
- **Large-repo indexing pass.** Indexing and update-path improvements covering type references, health, dynamic hints, and dead-code analysis. (#450)
- **Incremental duplication splice.** Update runs now splice duplication pairs incrementally instead of recomputing them wholesale. (#460)

### Documentation
- Plugin: version bump to 0.19.0 (no command/skill/hook/MCP-surface changes).

---

## [0.18.0] — 2026-06-08

### Added
- **MCP counterfactual token savings.** Every MCP tool call now records what its
  curated answer *replaced* — the raw file exploration the agent would have done
  otherwise — into the unified savings ledger as a `mcp:<tool>` row. `get_symbol`
  reports the whole file it sliced one symbol from, `get_context` the full files
  its skeletons stood in for, `search_codebase` a conservative floor per cited
  file; estimates undersell by design. The Costs hero now reads "N MCP queries
  answered" and grows per call, and `repowise saved --by source` surfaces the
  per-tool `mcp:*` breakdown. Recording is best-effort and never alters a tool's
  user-facing response.
- **Costs page savings hero.** The Costs page now leads with a results card
  showing every token and dollar repowise saved your coding agent, combining
  the `repowise distill` ledger with MCP tool-response savings that were
  already on disk but previously invisible (`source='mcp:*'` in the omission
  store). The dollar estimate is **priced at the agent's actual model** —
  detected from your local Claude Code / Codex transcripts (read-only,
  on-machine), falling back to a default rate when undetectable — because saved
  tokens are input the agent never had to read. Missed savings now read as an
  "unlock more" prompt rather than a footnote.

### Changed
- **`repowise init` always renders the compact banner.** The init splash now
  uses the compact owl variant (~60% of the old full-art width) on every
  terminal, so narrow shells no longer wrap it. (#423)

### Internal
- Shared UI/server building blocks consolidated so the web UI and downstream
  consumers stop duplicating code: `OwlLoader` and the design-token gate
  scripts move into the `@repowise-dev/ui` package, a dependency-free
  `@repowise-dev/ui/brand` constants export is added, and the community/
  architecture view builders are extracted from the server routers into
  FastAPI-free `services/` functions. No change to the install/serve UX or any
  endpoint's response shape. (#423)

---

## [0.17.1] — 2026-06-07

### Added
- **Official MCP Registry listing.** repowise is published to the
  [MCP Registry](https://registry.modelcontextprotocol.io) as
  `dev.repowise/repowise` (PyPI package, stdio transport), so MCP clients can
  discover and install the server from the registry directly.
- **Distill: stat-only diff filter.** `git diff --stat` output gets its own
  filter — the roll-up line plus the top-20 rows by churn — instead of
  slipping past the hunk-based diff filter raw (#414).

### Changed
- **Skeleton is the default context card for files.** `get_context` on file
  targets above ~80 lines now returns the smart skeleton (every signature,
  central bodies inlined) instead of the bare symbol list — measured strictly
  better per token. `compact=False` opts out; a `mostly_full` flag marks small
  files where a direct `Read` costs little more (#414).
- **`repowise init` defaults tuned.** LLM concurrency defaults to 10 (tiny
  repos 12, huge repos 5) across `init`, `update`, and `workspace add`; the
  LLM cost-gate confirm defaults to yes (the cost was already shown beside the
  coverage tier); page generation prints a hint that runs are resumable with
  `init --resume` (#412).

### Fixed
- **Semantic search lost embeddings on mid-size repos.** A whole generation
  level was embedded in one API request; file pages routinely blew the
  provider's per-request token cap, failed 400, and the failure was swallowed
  at debug level — fresh inits silently lost all file-page embeddings.
  `embed_batch` now chunks requests with failure isolation per chunk, and the
  loss (if any) surfaces as a warning with a `repowise reindex` repair hint
  (#414).
- **`repowise update` evicted pages from semantic search.** Regenerated pages
  were persisted to SQLite but never re-embedded, so every update drifted the
  vector corpus away from file pages. Updates now embed regenerated pages into
  the vector store; existing repos repair with `repowise reindex` (#414).
- **`search_codebase` ranking.** Decision records (short title-statements)
  no longer dominate design-noun queries — they're down-weighted unless the
  query is why-shaped; retrieval over-fetches before re-ranking; the `kind`
  filter runs before the limit cut so `kind="implementation"` can't return an
  empty list; pages without a backing file classify as `"doc"` (#413, #414).
- **`get_risk` calibration.** The 0–10 score no longer pins at 10.0 from
  transitive-dependent breadth alone (exponential file term + capped breadth
  term); co-change partners survive incremental updates instead of being
  wiped; files excluded via `.git/info/exclude` no longer leak into
  `will_break`; `directive.missing_tests` is scoped to the PR's changed files
  (#413, #414).
- **`get_context` contract.** Docs + freshness defaults are always returned —
  `include=["skeleton"]` no longer drops the summary and freshness card;
  signatures collapse onto one line (no leaked `\r\n` from CRLF files); module
  cards describe child files with their indexed summaries (#413).
- **Generated CLAUDE.md quality.** Word-boundary truncation in tables (no more
  mid-word chops), prose-only sentence extraction (list items and table rows
  no longer jam onto the architecture paragraph), guided-tour steps carry file
  paths again, the Owner column drops when no module has owner data, and tech
  stack detection ignores test fixtures / vendored repos and finds
  `tsconfig.json` in workspace packages (#413).
- **`repowise init` health-phase progress bar** moved from the first completed
  AST walk instead of sitting at 0/N through the pre-walk, and the duplication
  scan overlaps the pre-walk (#412).
- **Distill on Windows:** `cmd /c` wrappers are stripped during command
  normalization so native listings route to the file-listing filter, which now
  also accepts absolute Windows paths (#413).

## [0.17.0] — 2026-06-06

### Added
- **Distill — index-aware output distillation.** A new capability that
  compresses noisy command output before the agent reads it, errors-first and
  fully reversible. `repowise distill <cmd>` runs a command and prints a
  compact rendering (exit code preserved, every error line kept, raw output
  stashed behind an inline `[repowise#<ref>]` marker); `repowise expand <ref>`
  restores it, optionally filtered with `-q`. Eight content filters ship
  (test/build output, git status/log/diff, search floods, file listings,
  generic logs), measured at 60–90% token reduction on noisy commands with
  zero error-line loss. An opt-in Claude Code PreToolUse hook
  (`repowise hook rewrite install`, or the `repowise init` prompt) rewrites
  noisy agent commands to `repowise distill <cmd>` pending approval —
  ask-by-default, with per-repo / per-family `allow`/`deny` config; pipes,
  redirects, and compound commands are never rewritten. `repowise saved`
  reports tokens and estimated dollars saved (per-filter / per-day / per-source
  rollups), mirrored by a Distill savings card on the dashboard's Costs page.
- **Read intelligence: skeletons, stale-read notices, search digests.**
  `get_context(..., include=["skeleton"])` returns an indexed file with bodies
  elided — every signature plus the bodies of the most central symbols, sliced
  from persisted symbol bounds with zero query-time parsing (~15% of full-file
  tokens). The PostToolUse hook nudges once per file per session when a large
  `Read` could have been a skeleton, warns when a re-read follows an
  `Edit`/`Write` (excerpts predate the edit), and renders grep floods as a
  compact grouped-by-file digest ordered by graph centrality.
- **Reversible MCP truncation.** Tool responses were always token-budgeted;
  truncation is no longer silent. Dropped content is stored in the omission
  store and surfaced via a `_meta.omitted` envelope (`refs`, `tokens`,
  `restore`); `get_symbol` resolves `repowise#<ref>` omission refs (with an
  optional `query` parameter) alongside symbol ids — the tool count stays at
  nine. One durable store (`.repowise/omissions/`, TTL + size-cap pruned)
  serves the CLI, the hook, and MCP.
- **Distill config & doctor checks.** A `distill:` block in
  `.repowise/config.yaml` (master switch, hook permission posture, per-family
  overrides, disabled filters, omission-store TTL/size). `repowise doctor`
  validates the block, reports omission-store size against its cap, and shows
  rewrite-hook install state.
- **Distill on Codex CLI.** The rewrite hook now supports Codex CLI alongside
  Claude Code, with repowise-command corrections (#391). A lint filter joins
  the filter set, `repowise saved` discovers missed savings, and the ledger
  tags savings per surface (#390).
- **Multi-language import resolution.** Lightweight per-language import
  resolvers with same-scope linking and spec metadata sharpen the dependency
  graph across the language registry (#392).
- **Light-default design-token theme system.** The web UI moves to a
  design-token theme with light as the default and a dual-theme component
  sweep (#405).
- **Owl mascot init banner.** `repowise init` opens with the owl mascot and a
  repo-seeded heatmap wordmark (#364), plus a clearer mode panel and
  searchable model selection (#379).
- **Agent-provenance layer in the git indexer.** Commit indexing records a
  deterministic agent-provenance layer (#366).
- **Claude Code plugin.** A `repowise` Claude Code plugin and root
  marketplace, refreshed to the current command/skill/MCP surface (#356).

### Changed
- **`repowise init` defaults the distill rewrite-hook prompt to yes** (#409),
  and every init flow records the verdict (#382).
- **Indexing and incremental updates scale with change size.** Parsing and
  betweenness results are cached across incremental updates (#369, #368),
  workspace indexing routes already-indexed repos through the incremental
  path (#384), and filesystem walks prune nested repos and junk trees (#380).

### Fixed
- **`get_context` hardened** — segment-boundary partial matching, git-file
  fall-through, and batch isolation (#401).
- **Distill correctness:** Grep-rescue fixes, PowerShell hook coverage, a
  nudge floor, and allowlist seeding (#389).
- **Health scoring:** `duplication_pct` computed from the union of clone
  ranges (#388), whole-file NLOC for file metrics instead of function-body
  sums (#387), and hotspot/ownership signals calibrated for small teams and
  quiet repos (#363).
- **Submodules:** persisted include-submodules flags are honored in health,
  dead-code, incremental updates, and upgrades (#383, #381).
- **Dead code:** local Express route middleware rescued from unused-export
  detection (#386); explicit relative JS imports resolve (#376).
- **Process hygiene:** MCP orphan watchdog, live-PID update locks, and
  PATH-hijack-proof registration (#385).
- **Generation:** never-started page coroutines are closed on cancellation
  (#365).
- **Server:** jobs honor `exclude_patterns` and prune stale rows (#354);
  breadcrumb path labels are decoded in the web UI (#359).

### Dependencies
- starlette 0.52.1 → 1.0.1 (#367).

---

## [0.16.0] — 2026-06-03

### Added
- **Codex CLI provider and project integration.** A new local Codex CLI LLM provider runs documentation generation through authenticated `codex exec` sessions (argv-based subprocess, JSONL parsing tolerant of non-JSON noise, async concurrency cap, exec timeout, and zero-cost subscription usage tracking). Adds project-local Codex setup — a `.codex/config.toml` MCP server, `.codex` lifecycle hooks, `.codex-plugin` metadata and marketplace entry, and managed `AGENTS.md` generation. Reasoning effort is now wired across all LLM providers with per-provider model discovery and supported reasoning modes (#348).
- **Native Ollama embedder.** Semantic indexing can now embed through a local Ollama instance directly, without routing through an OpenAI-compatible shim (#331).
- **`repowise init --resume` actually resumes.** Persistence is split into per-phase persisters (ingestion, git, analysis, generation) so a re-run skips phases that already completed instead of redoing the whole pipeline. Public API and end-state are unchanged (#343).
- **Advisory CLI-version check in `repowise doctor`.** `doctor` now shows current vs. latest published version and the exact install-method-aware upgrade command (uv tool / pipx / pip / editable). Advisory only — it never auto-updates, never flips doctor's pass/fail, and swallows network errors (#346, closes #338).

### Fixed
- **Owner "last touched" reflects your own last commit.** Previously a teammate's commit to a file you co-own bumped your "last touched" timestamp. Each author's own first/last commit timestamps are now recorded and aggregated, and author identity is read through git's `.mailmap` so one person's multiple names/emails fold into a single contributor (#349).
- **`repowise update` re-runs health when config changes even if git is unchanged.** Editing `exclude_patterns` or `health-rules.json` used to have no effect until a code change touched each file. `update` now fingerprints the config and triggers a full health rescore when it changes (#337).
- **Minified/generated bundles can no longer wedge `init`.** Duplication detection's O(k²) window comparison could explode on checked-in minified chunks, leaving `init` stuck at `health 0/N`. New layered resource guards skip minified files, cap per-file tokens and the repo-wide window budget, and drop degenerate hash buckets (#342, closes #341).
- **`exclude_patterns` are enforced in MCP tool responses at query time.** Rows that predate an `exclude_patterns` change are now filtered out of every structured tool (context, answer, search, health, overview, dead_code, risk, and the rest) and out of aggregate KPIs, so excluded files never leak back into results or numbers (#339, #340).
- **User-added MCP `env` survives re-registration.** `repowise init`/`update` re-registration did a shallow replace of the `repowise` MCP server entry, silently wiping any user-added `env` block (BYOK provider/embedder keys) and degrading semantic search to the mock embedder. Server definitions are now deep-merged (#336, fixes #307).
- **Cost tracking no longer wedges `repowise update` on `database is locked`.** A second cost-tracking engine inserting per LLM call lost WAL's single-writer race against the doc-generation writer, blocking the full busy-timeout per call and turning `update` into an effectively non-terminating run. Persistence is now best-effort, plus a `--no-cost-tracking` flag and `REPOWISE_NO_COST_TRACKING` env var to opt out entirely (#330, closes #326).
- **Rust sibling test modules are kept live** in dead-code reachability instead of being flagged as unused (#332).
- **`reindex` uses the shared database engine** rather than opening its own (#333).

### Documentation
- **README and linked docs revamped** for accuracy, with a sharpened tagline, reframed layer positioning, and fixed README/CONTRIBUTING links (#334, #335, #345).

---

## [0.15.2] — 2026-05-31

### Added
- **`on_page_ready` streaming callback.** `run_pipeline` / `run_generation` / `PageGenerator.generate_all` now accept an optional `on_page_ready` callback, invoked with each page the moment it is generated (alongside the existing `on_page_done`, which only receives the page type). Lets callers persist or stream pages incrementally — e.g. flush pages to storage per page so a generation cut-off yields a partial-but-usable set rather than nothing. Additive and backward-compatible; best-effort (a sink error is logged and never aborts a run) (#328).

---

## [0.15.1] — 2026-05-30

### Fixed
- **Misconfigured embedders no longer fail silently.** When an embedder is explicitly configured (`REPOWISE_EMBEDDER` or `.repowise/config.yaml`) but can't initialise — most often a missing API key — the MCP server used to fall back to the mock embedder with only a `WARNING`, then report healthy while semantic search (`search_codebase`, `get_answer`) ran on vectors that can't match the real index. The failure is now logged at `ERROR` with the missing key and remediation, and surfaced in every tool's `_meta` envelope (`embedder_degraded: true`) so it's detectable instead of masquerading as a healthy server. Embedder resolution also goes through the shared registry, so `openrouter` and custom-registered embedders are honoured — not just `openai`/`gemini` (#324).
- **Indexing artifacts serialize reliably.** A transient blame index is dropped before artifact serialization, fixing a failure that could corrupt published index artifacts (#323).

---

## [0.15.0] — 2026-05-30

### Added
- **Code-health biomarkers overhaul — calibrated, multi-language, process-aware.** The health model is reworked into a broad biomarker suite whose weights are calibrated against real defect data (#305): class-level cohesion and god-class detection (#302), test-quality smells with hardened size sensitivity (#303), change-entropy and co-change scatter signals (#301), ownership and relative-churn process signals (#300), and a prior-defect process signal (#312). Coverage deductions now scale by the uncovered fraction rather than a flat penalty (#314), primitive-obsession is suppressed in tiny modules to cut false positives (#313), and the biomarkers extend to Kotlin, C++, and C# (#316).
- **`repowise risk` — just-in-time change-risk scoring.** A new command and scoring pass estimate the risk of changing a file from churn, complexity, ownership, and defect history, surfaced both in the CLI and the dashboard (#315).
- **Commits change-risk page with per-function blame.** A new commits explorer surfaces per-commit change-risk history (#317), change-complexity and defect-history signals (#318), and a per-function blame view with a coverage-gradient breakdown (#319).
- **Coverage ingestion.** `repowise` ingests normalized-JSON coverage reports and surfaces coverage metrics across the health surfaces (#309).
- **`.mts` / `.cts` are treated as TypeScript** for indexing and language detection (#310).

### Fixed
- **`exclude_patterns` are enforced in the git indexer and dynamic-edge passes** — excluded paths no longer leak back in through commit history or dynamic edges (#308). Index-only runs now persist `exclude_patterns` via `save_config_partial` so subsequent updates honour them (#297).
- **Single-file re-index no longer wipes all dead-code findings** — an incremental re-index of one file previously cleared the whole findings set (#298).
- **CommonJS `require()` is resolved** so property-access calls on a required module are no longer mis-flagged as dead (#299).
- **`@repowise-dev/ui` uses extensionless relative imports** package-wide, fixing a web build failure where `.js`-suffixed relative specifiers failed to resolve (#320).

### Documentation
- **README header refreshed** with a banner and badges (#311).

---

## [0.14.0] — 2026-05-28

### Added
- **JVM (Java + Kotlin) indexing brought up to C# / Rust / Go parity — 5 PRs.** A `JvmPackageIndex` workspace model + Maven / Gradle root recognition treats Java/Kotlin packages as the unit of reachability (so helpers next to a `@SpringBootApplication` are no longer mis-flagged), annotation processors generate symbol stubs for `@Generated` companions, type-ref resolution mirrors the Go/Rust pattern (field/parameter/return types resolve against imports), and JVM dead-code hardening recognises framework annotations (`@Component`, `@RestController`, `@Configuration`, JPA `@Entity`, `@SpringBootTest`) as live-from-framework entries. Spring expansion, Jakarta, Quarkus, Micronaut, and Android-component edges are emitted along with dynamic-hints for reflective lookups, JNDI, and serialization (#273, #274, #275, #279, #280).
- **C++ indexing hardened across symbol graph, dead-code, and framework recognition — 6 PRs.** Workspace-aware include resolution walks the CMake/Bazel project tree to find headers (with a public-header rescue pass for `include/`-style layouts), the symbol graph captures lambda captures + type references + synthesized destructors and entry markers, dead-code reachability respects contracts (`[[nodiscard]]`, `[[maybe_unused]]`, virtual overrides) and never-flags `WebAssembly`/`Emscripten` exports, dynamic markers cover function pointers and reflective lookups, tests / benchmarks / fuzzers are recognised via gtest / Catch2 / Google Benchmark / LibFuzzer entry points (broad `tests/` glob, plural `benchmarks/`, embedded pybind11 modules), and compiler-builtin macros (`__has_attribute`, `__has_builtin`) no longer mask reachable code (#281, #282, #283, #284, #285, #286).
- **JS/TS indexing brought up to C# / Rust / Go parity.** Closes the gap with the strongly-typed languages — package-aware workspace model, type-ref resolution against `import type` statements, and dead-code never-flags for the standard JS entry points (#272).

### Fixed
- **`repowise init` no longer crashes mid-pipeline on Windows shells defaulting to cp1252.** Rich's legacy Windows renderer encodes every printed line through the active code page, so the first `↳` or `✓` glyph in the progress UI raised `UnicodeEncodeError` and aborted the run, leaving partial state behind. `sys.stdout` and `sys.stderr` are now reconfigured to UTF-8 with `errors="replace"` before any Rich Console is constructed, so old `cmd.exe` and PowerShell sessions render cleanly without needing `PYTHONIOENCODING=utf-8` (#290, closes #271).
- **Upgrading repowise no longer crashes with `no such column: decision_records.verification` (or similar).** `Base.metadata.create_all` only creates missing tables — it never ALTERs existing ones, so any user who indexed a repo with an older release and then `pip install --upgrade`'d would hit cryptic `OperationalError`s the moment a code path queried a column added by a later release. `init_db` now reconciles additive schema drift generically: walks every table in `Base.metadata`, adds any model-declared columns + indexes missing from the live schema, and synthesizes a DDL `DEFAULT` from a static Python `default=` value so NOT NULL back-fills onto populated tables don't fail. Any future column/index added to the model is picked up automatically with zero code touch (#292).
- **`repowise serve` no longer crashes when port 3000 is already taken.** If the user's project already binds the default web-UI port (3000) or API port (7337), the server now probes for the next available port within a 20-port scan window and prints a yellow "using N instead" notice, instead of aborting after a clean API startup with `EADDRINUSE`. Falls back to an OS-assigned ephemeral port if the whole window is busy (#287, closes #232).
- **`repowise serve` detects old Node.js and falls back gracefully.** Previously the only check was whether `node` was on PATH, so a stale Node.js (e.g. 12 on a WSL setup) slipped past the gate and then crashed the bundled Next.js 15 runtime with `SyntaxError: Unexpected token '?'`. The serve command now parses `node --version`, compares it to the minimum required by the web UI (Node 20+), and falls back to API-only mode with a clear "upgrade Node.js" message when too old (#289, closes #276).
- **MCP registration is workspace-aware.** `repowise init` against a sibling repo in a multi-repo workspace used to overwrite `~/.claude/settings.json` with the per-repo path, silently breaking workspace mode the moment a second repo was indexed. Registration now targets the workspace root when `.repowise-workspace.yaml` is found in any ancestor, so subsequent inits converge on the same entry and `repo="<alias>"` queries work across all repos (#278, closes #277).
- **`repowise init` "What's next" panel — spacing + headline command.** Long commands like `repowise init --provider gemini` (>28 chars) used to run straight into their description (`geminigenerate full documentation`) because the format spec only left-padded short commands. Always inserts at least one space now. For the index-only path, the headline next-step is `repowise serve` (which actually launches the dashboard) rather than `repowise mcp .` (which `init` has already auto-registered) (#291).

---

## [0.13.0] — 2026-05-27

### Added
- **Rust indexing brought up to C# parity — 26 fixes across 8 waves.** Cargo workspace member globs (`crates/*`) now resolve, visibility modifiers are captured for structs / enums / traits / consts / types / modules (distinguishing `pub(crate)` / `pub(super)` from `pub`), `mod foo;` declarations register as import edges, and `self::` / `super::` / `crate::` prefixes are recognised as relative imports. Rust never-flag patterns cover `build.rs`, `examples/`, `benches/`, `tests/`, `src/bin/`, and fuzz targets, plus dynamic-import markers for trait objects, FFI, serde, and conditional compilation. On Typst, dead-code findings dropped 1,013 → 244 and unreachable files 92 → 40 (#251).
- **Go indexing brought up to C#/Rust parity.** A new `GoPackageIndex` workspace model and package-index warmup phase resolve package imports to all files in the package, and a package-aware reachability pass computes reachability at package (directory) granularity rather than per file — so helpers sitting next to `main.go` are no longer mis-flagged. Go never-flag patterns cover test files, `cmd/*/main.go` and root `main.go` entry points, `doc.go` package-doc stubs, `magefile.go`, and generated code (`*.pb.go`, `*_string.go`, `*zz_generated*.go`, `*bindata.go`, `*_gen.go`, `*.gen.go`); `init` / `TestMain` are entry symbols (#267).
- **Wiki information-architecture upgrade.** The docs hub gains a semantic "By domain" tree (Guided Tour → Architecture → Modules → Reference) as the default with a "By folder" toggle, hierarchical breadcrumbs with sibling prev/next, an in-page table of contents, and a "Start here" front-door panel promoted from a collapsed section. Inline backtick references render as clickable internal links, a ⌘K / Ctrl-K full-text command palette searches the loaded page list, and a sidebar surfaces related pages (forward links) and backlinks ("Linked by"). Pages carry page-type and "in {module}" zoom-out chips and an inline low-confidence banner. All of it lives in the shared `packages/ui` surface (#238).
- **Decision layer overhaul — provenance, more sources, surfaced everywhere.** Decision extraction now mines four new sources (deterministic ADR auto-discovery, CHANGELOG mining, PR/squash-body mining, centrality-bounded comment archaeology) in addition to commits, whose multi-line bodies are now captured. A new `decision_evidence` table records one-decision-to-many-evidence with a source-ranking ladder and verification status; matching extractions merge into a single record that accretes evidence instead of discarding duplicates, and confidence is derived from source rank, corroboration, and verification. A post-extraction substring gate drops LLM fields not grounded in their verbatim source span (#239).

### Changed
- **Dead-code accuracy for embedded JS and C/WASM.** C functions exported across the JS↔WASM boundary via `EMSCRIPTEN_KEEPALIVE` / `WASM_EXPORT` macros or `__attribute__((export_name(...)))` / `((used))` are recognised as exported rather than dead (their caller is the host runtime). C/C++ type-reference resolution mirrors the Go/Rust strategy: struct / typedef / class types used as field, parameter, or return types now resolve against `#include`d headers, so header structs are no longer read as unused exports. Also fixes a C/C++ include resolver bug where a repo-relative path was resolved against the process CWD, causing it to miss and fall back to a wrong stem match (#268).
- **Web navigation restructure and docs performance pass.** Trend / Coverage / Refactoring fold back into Health, Security moves from a sidebar item to a tab under Risk (old route redirected), Docs is renamed Wiki and surfaced near the top of the nav, and the docs landing opens the repo overview by default with a collapsible "Start here". A global SWR config disables revalidate-on-focus/reconnect and dedupes requests, the full page list is fetched once instead of twice, and rendered wiki markdown is memoized so persona/sidebar toggles no longer re-parse the document (#240).
- **Internal restructuring across CLI, ingestion, generation, persistence, server, MCP, and web.** A sweep of large modules was decomposed into focused packages — `crud.py`, `schemas.py`, `tool_context.py`, `tool_answer.py`, `framework_edges.py`, `context_assembler`, `routers/graph.py`, the pipeline phase functions, the CLI `ui.py` / `init` / `update` commands, the language specs, and the web `api/types.ts` — with no change to install or serve behaviour (#246–#265).

### Fixed
- **TS/JS re-export barrels no longer flag forwarded symbols as dead.** `export { X } from "./x"` and `export * from "./y"` now produce graph edges, so a component reached only through an `index.ts` re-export chain (the standard component-library barrel) is correctly seen as used. `index.*` / `__init__.py` barrels are skipped in the unreachable-file pass without affecting the unused-export pass, so a real symbol defined in a barrel is still flagged (#245).
- **Python imports that hid live code as dead are now resolved.** A source-root-aware module index resolves absolute imports under nested or namespace source roots (src layout, `packages/*/src`, PEP 420), aliased namespace imports (`from . import levels as _levels`) record the module name so bare-relative expansion works, and a dynamic-import hints extractor emits edges for `importlib`/string-based plugin registries (#244).
- **`--format json` / `md` output is no longer polluted by logs.** `repowise health` and `repowise dead-code` emitted structlog/stdlib info/debug lines on stdout, making the output unparseable by `jq` and other consumers; logs are now silenced before the ingestion pipeline starts whenever the format is not `table` (#242).
- **Changelog decision source discovered under `docs/`, `doc/`, `.github/`.** Decision extraction only globbed the repository root, so projects keeping their changelog under `docs/` had that entire source silently skipped; the conventional documentation subdirectories are now searched (root still first) (#243).
- **No more "Event loop is closed" traceback at the end of `repowise init`.** All five LLM providers recorded per-generation cost via a fire-and-forget task that could outlive the `asyncio.run` loop; cost recording is now awaited inline (#241).
- **Reindexing after an embedder change no longer fails with an opaque LanceDB error.** Switching embedders (e.g. a mock dim 8 → OpenAI dim 1536) left the `wiki_pages` table on the old vector schema, so every write failed deep inside LanceDB with an IO error that never mentioned dimensions; the table is now dropped and recreated when the stored vector dimension differs from the current embedder's output (#266).

### Chore
- Added `.well-known/funding-manifest-urls` so the `funding.json` manifest can verify repository ownership for the FLOSS/fund directory (#237).

---

## [0.12.0] — 2026-05-25

### Added
- **Knowledge Graph visualization — full C4 revamp.** The C4 diagram page is renamed to "Knowledge Graph" across all user-facing surfaces (sidebar, breadcrumb, page headers). Edges now resolve to distinct warm-palette colors by relationship type with animated flowing dashes, relationship labels ("imports", "depends on"), and arrowhead markers. Nodes are larger with colored left accent borders via the tone system and complexity badges on layer cards. Selecting a node dims unrelated nodes to 25% opacity. Backend adds an architecture view API, DB-first layer/tour loading, a KG enrichment pipeline with fingerprint-based skip logic, and file-level health scoring (#235).

### Changed
- **Near-linear scaling restored for .NET import resolver.** Three independent algorithmic fixes — bucketing files by project reduced from O(N × M × depth) to a single parent-chain walk against a precomputed dict; type-ref ranking memoises `Path.resolve()` per source file; using-directive resolution caches the importer path. Combined dotnet phases drop ~70% wall-clock on a 2000-file synthetic repo, and scaling at 4× file count goes from ~20× to ~7.4× (#233).

### Documentation
- Condensed benchmark section to a single paragraph (#231).
- Refreshed README, added COMMERCIAL.md, fixed layer/tool/biomarker counts (#230).

---

## [0.11.0] — 2026-05-24

### Added
- **Fast index mode + incremental fast→full upgrade.** `repowise init --mode fast` does a quick first pass on very large repos — builds the dependency graph and indexes only the *essential* git tier (last commits, no per-file blame or co-change), and skips LLM doc generation. `repowise update --full` then upgrades that index to a full one **incrementally**: it backfills the git tier to FULL (per-file blame + repo-wide co-change) via a resumable, checkpointed worker, rehydrates the persisted graph from SQL rather than re-parsing and re-resolving it, and generates the docs fast mode skipped. Because the expensive import/call/heritage resolution and centrality computation are reused, the upgrade is measurably cheaper than re-running a full `init` (~14× faster on the avoided structural work at 2k files). The backfill is resumable — an interrupted `update --full` picks up where it left off (#220, #224).
- **Four new code-health biomarkers.** `hidden_coupling` flags pairs of files that consistently change in the same commits without an explicit import/dependency edge — behavioral coupling static analysis can't see. `complex_conditional` catches branch/loop guards combining three or more boolean operators (severity grows with operator count). `function_hotspot` flags functions that are both structurally complex and frequently modified, and `code_age_volatility` flags old, settled functions that are suddenly being edited — both computed from a per-line blame index built once per file. The three git-derived biomarkers are tier-aware: they no-op on an ESSENTIAL-tier (fast) index and light up once the FULL git tier is present (#221, #222).
- **Pluggable storage seams + capability registries.** New async `IndexStore` / `GraphStore` / `JobStore` interfaces with SQL/in-process default implementations, and process-wide `cli_registry` / `mcp_tool_registry` / `pipeline_hooks` registries so downstream packages can extend the CLI, MCP tool list, and pipeline phases without monkey-patching internals. Behavior is unchanged for OSS users — same CLI commands, same MCP tools, same default storage (#219).

### Changed
- **Code-health scoring recalibrated.** The organizational category cap is lifted from −1.0 to −3.5 so the strongest empirical predictors (`developer_congestion`, `untested_hotspot`, `hidden_coupling`) are no longer suppressed, and a per-biomarker weight multiplier was added (`developer_congestion` ×1.5, `untested_hotspot` ×1.3, `function_hotspot` ×1.2). `knowledge_loss` is de-rated to ×0.4 per OSS calibration (legacy code that works gets handed off) — enterprise users can raise it back via per-repo overrides. See the updated category-cap table in `docs/CODE_HEALTH.md` (#221).
- **Health web UI surfaces the new biomarkers.** Glossary entries and biomarker-specific detail views for all four new biomarkers (partner-file chip for `hidden_coupling`, operator count for `complex_conditional`, mod/p80 ratio for `function_hotspot`, median age + recent edits for `code_age_volatility`), recalibrated category caps in the score breakdown (now sorted by applied deduction), and a clickable `function:line` deep-link in the file drawer. The new biomarker details also flow into the AI refactor prompt (#223).
- **Doc generation scales to very large repos.** Batch embedding (one model call per generation level instead of N upserts), pipeline checkpoint/resume over the new JobStore seam, and graph metrics computed in SQL. Default behavior is unchanged when the new knobs aren't set (#220).

### Fixed
- **`repowise update --full` now recomputes code health at the FULL tier.** The upgrade backfilled the git tier and regenerated docs but never re-ran the health analysis, so the persisted health tables stayed frozen at the fast index's ESSENTIAL-tier findings — the blame/co-change biomarkers stayed invisible after an upgrade. The upgrade now runs a full-repo health pass against the rehydrated graph and persists findings/metrics/snapshot, matching what `init` and the normal `update` path do (#225).

### Documentation
- README mentions the Repowise PR Bot in the hosted-version section (#217).

---

## [0.10.0] — 2026-05-18

### Added
- **Code health layer — a fifth intelligence layer alongside graph / git / docs / decisions.** New `repowise health` command, `health_*` SQLite tables, biomarker engine, and `/repos/[id]/health` web UI surface what hotspots actually *cost* to maintain. Tree-sitter complexity walker feeds 10+ biomarkers across structural (`large_method`, `nested_complexity`, `bumpy_road`, `complex_method`, `brain_method`, `primitive_obsession`), organizational (`developer_congestion`, `knowledge_loss`), test (`untested_hotspot`, `coverage_gap`), and DRY (`dry_violation`, backed by a tokenizer + Rabin-Karp clone detector with co-change correlation) categories. A composite 0–10 score is rolled up per file, per module (NLOC-weighted), and per repo. Findings persist with deterministic refactoring suggestions and an `acknowledged | resolved | false_positive` lifecycle. `HealthSnapshot` writer + trend detector surface declining-health alerts. `.repowise/health-rules.json` supports per-file overrides. `repowise update` runs an incremental upsert so the dashboard stays fresh without re-running the full pipeline. New `repowise health --trend`, `--refactoring-targets`, `--module`, `--file`, and `--coverage` flags; `repowise status` prints a one-line health digest. Parallel biomarker analysis via `asyncio.gather`. Full architecture deep-dive in `docs/CODE_HEALTH.md` (#212).
- **Coverage ingestion (LCOV / Cobertura / Clover).** `repowise health --coverage report.lcov` parses one or more reports (format auto-detected, override with `--coverage-format`), persists per-file line + branch + covered-line sets to a new `coverage_files` table, and feeds two coverage-aware biomarkers — `untested_hotspot` (hotspot files with low coverage *or*, when no coverage is ingested, no paired test file) and `coverage_gap` (significant uncovered surface area on non-test files). New `/api/repos/{id}/health/coverage` endpoint + `/repos/[id]/health/coverage` page with risk × coverage scatter, module rollup, and per-file drilldown (#212).
- **Code-health web UX overhaul — tabbed chrome, trend, scatter quadrants, file drawer.** `/health` is now a four-tab surface (Overview / Trend / Coverage / Refactoring) with shared `HealthPageChrome`, sparklines pulled from `/health/trend`, a 5th "Hotspot Health" KPI card, server-side paginated file table with sortable headers + filter chips (Hotspots / Untested / Failing) + path search, biomarker glossary tooltips, severity-distribution bars, slide-over `HealthFileDrawer` with score breakdown by category, impact × effort quadrant on the refactoring page, and one-click status mutation (Acknowledge / Resolved / False positive) wired to a new `PATCH /health/findings/{id}` endpoint. Inline `HealthBadge` chips appear on the hotspots / ownership / graph views so health context follows you across the app (#212).
- **AI fix / test prompts on the refactoring and coverage pages.** Per-row `AI fix prompt` / `AI test prompt` buttons open a modal that picks a target agent (Generic / Claude Code / Cursor), previews the generated prompt (with biomarkers, line ranges, severities, score deductions, suggested directions, hard constraints, completion contract — and the explicit "verify each finding against the real code; treat analyzer output as leads, not ground truth" preamble), and copies to clipboard. Prompt builder is generic (`buildAiPrompt` / `buildCoverageAiPrompt`) so future surfaces can reuse it (#212).
- **New `get_health` MCP tool + health enrichment on existing tools.** `get_health(include=['coverage'])` returns the score + biomarker breakdown + coverage summary; `get_context(include=['health'])` surfaces per-file score, top two biomarkers, and linked coverage row; `get_risk` rows gain `coverage_pct` / `branch_coverage_pct`; `get_overview` exposes hotspot-health KPIs. Auto-generated `CLAUDE.md` gains a Code Health section listing critical biomarkers so agents see the health context on every invocation (#212).
- **C4 architecture diagrams (L1 System Context / L2 Containers / L3 Components).** New `/repos/[id]/c4` page (React Flow + ELK layout, URL-synced via `?level=` and `?container=` with nuqs), backed by `services/c4_builder/` and three endpoints under `/api/graph/{repo_id}/c4/{l1,l2,l3}`. Container detection re-uses manifest paths from a new `external_systems` table (manifest parsers for npm / PyPI / Cargo / Go / NuGet that capture name + version + ecosystem + heuristic category). Containers fall back to top-level dirs on repos without manifests. L3 components are subdirs inside a container. Inspector panel surfaces module-health context per component. SVG / PNG / Mermaid export menu with a `/c4/mermaid` server endpoint. Shared UI lives in `packages/ui/src/c4/` so the hosted product can reuse it (#204, closes #203).
- **MCP tool surface bumped 7 → 8 with `get_symbol` exposed.** `get_symbol("path::Name")` returns raw source bytes for one indexed symbol with exact line bounds — cheaper and safer than `Read` + offset math. `get_context` was trimmed to a triage card (title, summary, signatures, `hotspot` bit, `decision_records` titles, `symbol_id` pointers) — the `include=["source"]` mode was removed; agents should pipe `symbol_id` into `get_symbol` instead. `get_risk` PR mode now emits a structured `directive` block (`will_break` / `missing_cochanges` / `missing_tests`) with capped co-change / transitive lists. `search_codebase` gains a `kind` filter and a per-result `search_method` (`embedding` vs `bm25` fallback) plus a bareword-identifier grep hint. Every response carries an `_meta` envelope (`index_age_days`, `indexed_commit`); a `stale_warning` fires only when the indexed HEAD actually diverges from `.git/HEAD`, so silence is trustworthy (#210).
- **`get_answer` rewritten as a hybrid retrieval pipeline.** FTS + vector store run in parallel, merged via reciprocal-rank fusion, PageRank-biased, expanded one graph hop to rescue near-misses, fused with decision records on "why"-shaped questions, and prepended with a structured prelude (top symbols, recent significant commits, decision titles). Confidence and `retrieval_quality` report independently so synthesis quality and retrieval quality don't get conflated. Low-confidence returns now ship `best_guesses` with one-line justifications instead of an empty answer. Schema-versioned cache auto-invalidates earlier-pipeline payloads (#210).
- **Doc-generation upgrade — enforced coverage budget, faster runs, wiki interlinking.** New `generation/selection/` package is the single source of truth for which pages get emitted; `PageGenerator` and `cost_estimator` both consume it, so the pre-run estimate can no longer drift from the actual run. `GenerationConfig.coverage_pct` (default 0.20) is the user-facing knob, with per-bucket shares across `file_page` / `symbol_spotlight` / `module_page` / `api_contract` / `infra_page` / `scc_page` — no more bypass paths around the budget. New interactive coverage chooser shows per-bucket counts and a cost range (10 / 15 / 20 / 30 / 40 / 50 %) computed from live ingestion data, with self-calibration from prior runs' `wiki_pages`; `--coverage` CLI flag for non-TTY use. Prompt caching via provider-agnostic `CacheHint` (Anthropic emits `cache_control`, OpenAI surfaces `cached_tokens`). Persistent cross-run page cache short-circuits the LLM call when `source_hash + model` match. Module pages now group by graph community (default `min_module_size=3`) instead of top-level dir — handful of generic per-directory pages → 30–80 genuinely scoped module pages on large repos. Dead-code findings, decisions, and external systems now flow into `file_page` / `module_page` / `repo_overview` contexts. New `api_contract_detector` routes FastAPI routers and ASP.NET controllers through the dedicated `api_contract` template (#208).
- **Onboarding documentation collection — 8 curated pages, default-on at `repowise init`.** New `generation/onboarding/` subpackage emits up to eight gated subkinds (`codebase_map` always; `getting_started`, `key_concepts`, `how_it_works`, `development_guide`, `active_landscape` gated on signals like manifest presence, PageRank P90 symbols, execution-flow depth, suffix patterns, recent commit volume) plus two promoted slots (`project_overview`, `architecture_guide`) that reuse the existing `repo_overview` / `architecture_diagram` pages via `metadata.onboarding_slot`. UI renders an "Onboarding" folder at the top of the docs tree (Compass icon, auto-expanded, canonical slot order). `--onboarding` / `--no-onboarding` flag on `repowise init`, persisted to `config.yaml` (#208).
- **Wiki interlinking.** Post-gen regex scan resolves backtick refs to other pages' `page_id`s and populates `metadata.wiki_links` + reverse-index `metadata.backlinks`. New `WikiLink` MDX component renders resolved refs as clickable anchors; new `BacklinksPanel` in the wiki sidebar surfaces pages linking into the current one (#208).
- **Pipeline progress for previously silent phases.** Added phase events around `tsconfig` (TS path-alias resolver init), `dynamic_hints` (HintRegistry edge extraction), and `external_systems` (manifest parsing). The two graph aggregations (`graph.metrics`, `graph.communities`) now emit per-algorithm completion lines as each `asyncio.gather` task finishes — output makes it obvious which algorithm is the bottleneck (almost always betweenness on the symbol graph for medium+ repos) (#206).

### Changed
- **Dead-code analyzer cuts ~390 false positives across resolver, parser, and analyzer.** Alembic migration scripts under `*/alembic/versions/*.py` are never-flagged (reflectively loaded). Click / Typer decorators on locally-named groups (`@my_cli.command("add")`) are now recognised via suffix matching. `unused_internal` counts an incoming `imports` edge whose `imported_names` lists the symbol — catches dispatch-table patterns (`HANDLERS = {"python": _extract_python, ...}`). Entry-point allowlist extends to WSGI / ASGI / Flask / FastAPI factory conventions (`create_app`, `make_app`, `application`, `get_asgi_application`, …). Bare relative Python imports (`from . import a, b`) now expand into per-name `Import` objects so plugin barrels resolve. Symbol extraction skips any AST node nested inside a callable, so React handler closures and async-method-local coroutines no longer hoist to the top-level symbol list. `.tsx` files now parse with tree-sitter's JSX-aware grammar. JSX elements (`<Component />`) register as call sites for the named component. Public symbols in files imported as namespaces (`from . import cargo`, `import * as cargo from "./cargo"`) are rescued — the static graph can't tell which attribute is being dispatched, so flagging individuals yields guaranteed false positives. TS workspace resolver honours `package.json#exports` (conditional, wildcard, longest-prefix) so turborepo / nx / pnpm monorepos resolve through subpath exports — verified `−23 %` dead-code findings on the dogfooded monorepo. Win32 entry points (`wWinMain`, `WinMain`, `wmain`, `ServiceMain`, `LowLevelKeyboardProc`, MSTest macro family) and never-flag globs for precompiled-header anchors (`pch.h` / `stdafx.cpp`), COM `*ClassFactory.cpp`, and broader test-project conventions skipped on C++ codebases — roughly 520 high-confidence findings cleared on PowerToys (#194, #207).
- **Ingestion treats nested git repos as traversal boundaries by default.** When a working tree physically contains other independent git repositories as subdirectories (workspace roots that are themselves versioned, sibling repos checked out under a parent), the traverser walked into them and pulled in their entire file trees. Now a `.git` entry (directory, submodule file, or external gitdir pointer) is a hard traversal boundary. Opt-in `include_nested_repos=True` preserves the old behaviour. New `skipped_nested_repo` counter surfaced in the filtering summary (#205).
- **CLI editor setup refactored into an integrations package** with per-editor strategy classes (Claude / Cursor / generic) so adding a new editor is a new file rather than edits across the suite (#199).
- **Dynamic-hint extraction is no longer the wall-clock-stall phase.** The 13 dynamic-hint extractors used to call `repo_root.rglob(pattern)` independently — each descended into `node_modules`, `.venv`, `.next`, `__pycache__`, and on Windows followed directory junctions into infinite loops. New `_walk.iter_glob` helper does `os.walk` with in-place dirname pruning, `followlinks=False`, realpath-based cycle detection, and a hard depth cap of 64. `HintRegistry.extract_all` runs the 13 extractors in a `ThreadPoolExecutor`. Walk completes in ~2 min on polyrepos with recursive junctions vs. the prior indefinite stall (#208).
- **Embedder no longer blocks the LLM critical path.** Post-LLM embed-and-upsert spawned as a background task so the next wave's LLM calls start immediately; the level still drains pending embeds before advancing so the next level's RAG search sees a fully-indexed store. New `enable_rag_context` and `rag_min_store_size` config knobs short-circuit the RAG search on cheap models and on early pages before the store has enough indexed material to return useful hits (#208).
- **OpenAI default model bumped from `gpt-4.1` to `gpt-5.4-nano`** in both the interactive provider picker and the web settings placeholder, to match the in-app cost-tier recommendation (#208).
- **Tighter README tagline and corpus framing.** New tagline: *"The codebase intelligence layer for your AI coding agent."* Drops the misleading "500 commits" references (the cap is per-file, not a global corpus cap) and softens engineering-team-only framing so solo devs see themselves in the README (#196). Refreshed `webui.gif`, compressed 16.6 MB → 8.5 MB (#198).

### Fixed
- **`repowise update` post-commit auto-sync rewrite.** The hook was racing with itself — concurrent invocations from rapid commits all started from the same stale base, took 12+ minutes each, never converged, and discarded output to `/dev/null`. `repowise update` now enforces single-flight via `.repowise/.update.lock`; if another update is running, the new invocation writes `.update.pending` with the current HEAD and exits, and the running update rolls forward to it. Hook pre-writes `.update.queued` synchronously before backgrounding so the augment hook sees an in-flight marker during the start-up window. Augment hook emits *"Wiki update in background — started Ns ago, target X"* instead of *"Wiki is stale"* when a marker is present. Stdout / stderr captured to `.repowise/.update.log` (rotated to 64 KB tail when it exceeds 256 KB) so silent failures are diagnosable. Hook installer upgrades the marker block in place when the body differs (previously bailed with *"already installed"* and left users stuck on the buggy hook after a repowise upgrade). Cross-platform — git always runs hooks under POSIX sh, so the same script body works on Linux / macOS / Windows (#211).
- **CI integration test failures introduced by the doc-generation PR.** `_TrackingProvider` mock didn't accept `cache_hints`; `_SlowVectorStore` mock didn't implement `get_page_summaries_by_paths`; `test_level_values_in_range` asserted level ≤ 7 but onboarding has been level 8 since phase 3. Also: `select_pages` now allocates all candidates when total supply ≤ budget (so `coverage_pct=1.0` on tiny repos returns pages instead of zero), and `score_file` applies a tiny per-symbol floor so leaf modules with zero PageRank still enter the candidate pool (#209).
- **Packaging: three `__init__.py` files silently dropped by a local `_*.py` exclude rule** in `.git/info/exclude` — `selection`, `cost_estimator`, `onboarding`, `external_systems`, and `dynamic_hints/_walk.py` all hit "module has no attribute" import errors on CI before this was caught. The `c4_builder` `__init__.py` was hit by the same rule and force-added in #204.

### Documentation
- **`CODE_HEALTH.md` user guide** + CLI reference entry + READMEs touched up to mention the fifth intelligence layer and the 8-tool MCP surface. The MCP-tools table now leads each row with *what only that tool answers* and surfaces the new signals (`retrieval_quality`, `best_guesses`, `search_method`, `hotspot` bit, `decision_records` pointer, PR-mode directive block, `_meta.stale_warning`) (#210, #212).
- **Removed `AUDIT_NOTES.md`** — internal scratchpad, not intended to ship in the public repo (#197).

---

## [0.9.1] — 2026-05-13

### Fixed
- **`repowise serve` 404 on the web tarball for v0.9.0.** The v0.9.0 publish workflow failed during the web build: two `packages/ui` components introduced this release (`attention-panel.tsx`, `co-change-list.tsx`) imported `useState` without a `"use client"` directive, so Next.js' RSC compiler rejected them when `packages/web` pulled them transitively via the overview page and wiki git-history panel. The Python wheel published to PyPI but no `repowise-web.tar.gz` was attached to the v0.9.0 GitHub release, so end-user `repowise serve` falls through to "API only". v0.9.1 adds the missing `"use client"` directive at the top of both files. **Anyone who installed 0.9.0 should upgrade to 0.9.1** to get a working web UI from `repowise serve`.

---

## [0.9.0] — 2026-05-13

### Added
- **Contributor profiles, module health, and reviewer suggestions.** New engineering-leader views composed from existing git metadata + dead-code rows. New endpoints `/api/repos/{id}/owners`, `/owners/{key:path}`, `/modules/health` (list + detail), and `/reviewer-suggestions?paths=` return paginated contributor directory, full per-owner profile (files owned, hotspots, dead-code burden, bus-factor risk, top files, co-authors, commit mix), composite 0–100 module-health scores, and confidence-ranked reviewers. New `/repos/[id]/owners` directory, `/owners/[owner]` profile, and `/repos/[id]/modules/[path]` pages in the web UI. Risk page gains a **Modules** tab, blast-radius results page renders ReviewerSuggestions in a side rail, ownership-treemap shows bus-factor borders (red ≤1, amber 2, green ≥3), sidebar gets a **Contributors** entry. New `@repowise-dev/ui` subpackages: `owners/`, `modules/` (#188).
- **Hotspot drill-down to top symbols.** Hotspot rows in the risk view now expand inline to show the importance-ranked top symbols in that file; clicking a symbol opens the existing SymbolDrawer. Backed by a new `file_path` filter on `/api/symbols` (#191).
- **Writable decision ↔ module linkage.** Decision detail page replaces the read-only affected-files block with a `ModuleLinkEditor` — module-path autocomplete pulls from `/modules/health`. `PATCH /decisions/{id}` accepts optional `affected_modules` / `affected_files` alongside `status` so editor saves don't force a status change (#191).
- **Truthful pagination across risk + symbols + git surfaces.** Hotspot, ownership, and symbol list endpoints now return a stable `{items, total, has_more, next_offset}` envelope; new shared `ResultsFooter` renders "showing N of M / load more" instead of silent client-side slicing. Hotspot cap raised from 100 to 500. `HotspotResponse` newly surfaces `commit_count_total`, `primary_owner_commit_pct`, `recent_owner_name/pct`, `merge_commit_count_90d`, `commit_count_capped`, `age_days`, `last_commit_at`. `git_indexer` no longer caps `top_authors` at 5 or `significant_commits` at 10 — both lifted to 50, exposed via the per-file git-metadata endpoint (#187).
- **Importance-ranked symbols workspace.** Server-side composite score combines file PageRank, visibility, complexity (log-normalised), kind, and entry-point status; transparent per-symbol component breakdown returned alongside each row. New filter facets — `visibility`, `in_hot_files`, `in_entry_points`. Per-row signal chips (visibility, entry-point, hot-file, complexity) and a file-context panel in the SymbolDrawer (owner, bus factor, churn state, co-changes, overlapping dead-code findings, blast-radius shortcut) (#187).
- **Graph signal enrichment + architecture endpoint.** New `/api/graph/{repo}/architecture` returns community super-nodes with per-cluster hotspot / dead / decision counts, doc coverage, and top languages. Full-graph export now capped to top-N by PageRank (configurable via `?limit=`, default 5000) with `truncated` + `total_node_count` in the response. Every graph response (full, architecture, module, ego, dead, hot) carries the same hotspot / dead-code / decision / docs signals. Module nodes aggregate `hotspot_count`, `dead_count`, `has_decision`, `primary_owner` from underlying files. Toolbar split into orthogonal **Scope** (Architecture / Modules / Full) × **Overlays** (Dead / Hot). New shared UI: `NodeBadges`, `GraphContextDrawer`, `GraphTruncationBanner` (#183).
- **`repowise delete` command + DELETE endpoint + dashboard button.** New CLI command lists repos in a numbered table, prompts for confirmation, then cleans FTS and CASCADE-deletes the repository and all child rows. New `DELETE /api/repos/{repo_id}` endpoint. Trash icon appears on hover in the dashboard repo list with a confirmation dialog. Supports `--force` and `--path/-p` on the CLI. Settings page redirects to `/` after delete instead of refreshing the now-404 route (#42).
- **Reasoning mode configuration** for LLM providers (#175).
- **Constructor + method parameter type-use edges for C#.** `csharp.scm` captures `@param.type` on ctor / method / delegate / record-primary declarations; a new `type_ref_resolution` module dispatches per-language strategies. C# strategy resolves names through `DotNetProjectIndex.type_map`, ranked by project enclosure. With these edges present, the universal `interface` skip in dead-code analysis narrows — now only excluded for Java / Kotlin / Scala (#180).
- **XAML dynamic-hint extractor.** Regex-parses `.xaml` / `.axaml` across WPF (`clr-namespace:`), WinUI / UWP / MAUI (`using:`), and Avalonia dialects. Resolves `x:DataType` and `DataContext` bindings against `DotNetProjectIndex.type_map` so ViewModels reached only via `{Binding}` no longer read as orphans. Also emits `dynamic_uses` edges for `<ResourceDictionary Source="..."/>` and `MergedDictionaries` entries (pack://, ms-appx:///, repo-rooted, and relative URIs) (#180, #186).
- **C# member-read resolution.** New `languages/csharp_member_reads.py` resolves `var x = new T()` and `this.Prop` to `reads` edges on the defining file. `nameof(Type)` emits `dynamic_uses` edges via `DotNetProjectIndex.type_map` (#184).
- **ASP.NET host-builder extension-method resolution.** `app.MapCatalogApi()` / `services.AddXxx()` resolve to their defining file by scanning `.cs` files for `public static T MapX(this <HostType> ...)` signatures against an allowlist of ASP.NET host types (`IEndpointRouteBuilder`, `IServiceCollection`, etc.). Host-builder extension scan now runs on any C# repo, not just ASP.NET Core (#182, #184).
- **CommunityToolkit MVVM synthetic symbols.** New pluggable `extractors/synthetic_symbols.py` per-language registry. C# entry synthesises `[ObservableProperty]` fields → PascalCase property symbols and `[RelayCommand]` methods → `<Name>Command` symbols (#186).
- **C++ qualified method definitions** (`void Foo::method() { … }`) now extract with `parent_name=Foo` and `kind=method`. New cpp.scm pattern for two-level `NS::Foo::method` declarations plus a parser helper that walks the `qualified_identifier` scope to recover the immediate enclosing type (#190).
- **C++ visibility refinement.** New `refine_cpp_visibility` reads access specifiers, file-scope `static`, and export attributes (`__declspec(dllexport)`, `__attribute__((visibility("default")))`); the latter sets a new `Symbol.is_exported_symbol` flag the dead-code pass uses to skip language-level exports. C++ heritage now classifies I-prefixed bases as `implements` and concrete bases as `extends` (#186).
- **Phase timings persisted to `state.json`.** New `PhaseTimingRecorder` `ProgressCallback` wrapper persists per-phase wall-clock durations to `state.json["phase_timings"]` so before/after perf comparisons no longer require external instrumentation (#182).
- **`type_use` edge provenance** persisted as its own edge type (was previously a NetworkX-only `via` attribute that the SQLite layer dropped) (#181).
- **`.xaml` / `.axaml` ingestion** as a passthrough LanguageTag so the traverser produces file nodes that XamlDynamicHints can attach edges to. The extractor was previously emitting edges that GraphBuilder silently dropped because the source path was not a known graph node (#181).
- **Detected tech_stack persisted** to `repositories.settings_json`. Adds generic WPF / WinUI 3 / Windows Forms detection from canonical SDK indicators. `tech_stack.py` replaces the root+1-level `.csproj` glob with a bounded depth-first walk (≤5 levels, ≤200 projects, `bin/obj` pruned) so monorepos with `src/<area>/<module>/<Project>/<Project>.csproj` layouts register correctly (#180, #181).

### Changed
- **Parse progress ticks fire per worker.** `asyncio.gather` held every `on_item_done` event until the last parse task completed, so the bar sat at 0/N for many minutes on large repos. Tick is now a done-callback on each task future — fires on the event loop thread as each worker returns (#183).
- **Co-change window widened** from 500 → 2000 commits; `min_count` dropped from 3 → 2, so low-churn repos surface pairs at all. Added funnel-stage debug log. `co_change` phase timer now closes the moment accumulation finishes via a new `on_co_change_done` callback (#180, #184).
- **Churn percentile contract normalised at the HTTP boundary** to 0–100 (was 0–1 in DB; UI consumers all assumed 0–100). `HotspotResponse`, `GitMetadataResponse`, symbol `file_churn_percentile`, and git-summary average all render correctly without per-component workarounds. Scatter defensively accepts the legacy 0–1 shape too (#187).
- **Churn × bus-factor scatter** surfaces a danger-zone count badge and a clickable legend of the riskiest files; shows an explanation when churn is uniform across the repo instead of a degenerate vertical strip (#187).
- **Hot Symbols Board collapsed by default** on `/symbols` — was a preview competing with the ranked table below (#187).
- **Single repo-wide `git log --numstat` pass** replaces O(files) per-file subprocesses; each worker reads commits from a shared in-memory dict. Per-language timing logged inside the `graph.imports` loop. 200-file cap in `_flush_commit` guards against mass-edit OOM (#184).
- **`DotNetProjectIndex.build_index`** collapses three overlapping `*.cs` walks into one master rglob with cached file texts shared between the namespace-map and global-usings passes. Expected speedup: ~40 min → ~5–8 min on Windows for the import-resolution phase on monorepos. No data quality loss (#181).
- **Dead-code never-flag patterns** picked up `Generated/` output, `*NativeMethods.cs` P/Invoke surfaces, ETW Telemetry/Events folders, merged-resource XAML dictionaries (Themes/Styles/Resources), standard test-project globs (`*Tests/*.cs`, `*.UnitTests/*.cs`, `*FuzzTests/*.cs`, `*UITest*/*.cs`, `*Tests.cs`), and `*/unittests/*.cpp|.h` (#180, #190).
- **Dead-code unused_export pass** treats an incoming `calls` / `method_implements` / `reads` edge on the symbol itself as evidence of life (was checking only file-level `imported_names`, which missed intra-file C++ helpers and qualified `Foo::method` definitions) (#190).
- **`dynamic_hints/dotnet.py`** learned `typeof(TypeName)` — catches `[JsonConverter(typeof(X))]`, `[TypeConverter(...)]`, `DataTemplate.DataType = typeof(X)`, and manual DI registration (#190).

### Fixed
- **`ResultsFooter` optional props** widened to `boolean | undefined` so `exactOptionalPropertyTypes: true` in the `packages/ui` tsc pass accepts call sites that pass `loading={maybeUndef}`. Unblocks `publish-internal.yml` which had been failing on the last two main pushes (#189).
- **Graph community panel uncloseable.** `CentralityLeaderboard` (z-10) was rendered unconditionally on top of the community panel (also z-10) and physically covered the X. Auto-mounted leaderboard dropped entirely — the inspection panel already shows pagerank / betweenness / degree percentiles for the selected node in-context. Community panel bumped to z-20; new `onCommunityPanelOpen` callback fires when the legend triggers the community panel so doc + community never stack (#183).
- **Cap unused-export confidence to 0.4** for `kind=interface` symbols with no incoming `implements`/`extends` edges. Implementor detection is heuristic across all statically-typed languages; absence is evidence-missing, not evidence-of-absence. Generic across C#, Java, Kotlin, Scala, Swift protocols, TS interfaces (#181).
- **Cap dead-code confidence to ≤0.4** for COM / IUnknown contract methods (`QueryInterface` / `AddRef` / `Release` / `IDispatch`) in C++/C#/Rust — dispatched through native vtables and never observable via static call edges (#182).
- **Add Windows DLL entry points** (`DllMain`, `DllGetClassObject`, `DllCanUnloadNow`, `DllRegisterServer`, `DllUnregisterServer`, `DllGetActivationFactory`) to the never-flag list (#186).
- **Phase 1b progress bar.** `graph.type_refs` wrapped with `on_phase_start/done` so the CLI bar no longer appears frozen between import resolution and heritage resolution on large .NET repos (#181).
- **Module clicks** across the app (Risk → Modules tab card, Heatmap treemap, Owner profile module rollup) now route to `/repos/[id]/modules/[path]` instead of `/ownership?module=…` which the old page ignored (#188).
- **`SafeToDeletePile` preview** groups findings by `file_path` with a finding count, so files with multiple dead-code findings no longer appear multiple times in the top-5 strip (#187).
- **Top Contributors card** removed from the Hotspots tab; it duplicated the Heatmap tab's contributor surface (#187).
- **Symbol bloat warning.** Parser logs a `parser.symbol_bloat` warning when a single file emits more than 500 symbols (#186).

### Dependencies
- `urllib3` 2.6.3 → 2.7.0 (#176)
- `mermaid` 11.13.0 → 11.15.0 (#177)
- `next` 15.5.15 → 15.5.18 (#178)

---

## [0.8.0] — 2026-05-11

### Added
- **Workspace mode is now first-class across the CLI.** Every relevant command auto-detects whether it's running inside a workspace root and routes accordingly, with a one-line `[workspace] …` notice when it does. New flags `--no-workspace` (force single-repo) and `--repo <alias>` (scope to one repo) on `update`, `status`, `watch`, `doctor`, `costs`, `search`, `dead-code`, `decision`, `generate-claude-md`, `hook install/status/uninstall`. `costs` and `search` also gained `--all` for explicit workspace-wide fan-out. New `Workspace auto-detect` section in [CLI Reference](CLI_REFERENCE.md) (#173).
- **`repowise update --workspace` now first-time-indexes previously-skipped repos.** Workspace entries without `.repowise/` no longer short-circuit with `"not_indexed"` — the full index pipeline runs (no LLM cost), `state.json` is written with a `docs_skip_reason` marker, and subsequent `update --repo <alias> --docs` cleanly picks up doc generation (#173).
- **`repowise workspace add` defaults to full index + LLM doc generation** when a provider is configured. Inherits provider, model, embedder, and exclude patterns from the primary repo's `.repowise/config.yaml`. `--no-docs` / `--no-index` opt out. Cost-gate prompt still runs before any tokens are spent (#173).
- **`repowise doctor --workspace`** validates every workspace entry: directory exists, has `.git/`, state.json ↔ workspace config drift, MCP registration. `--repair` syncs drifted entries from disk and drops dead entries whose directory no longer exists (#173).
- **Honest completion summaries.** `init` and `status` now print a per-repo Docs status block listing whether docs were generated, the skip reason (`cost gate declined`, `provider failure`, `index-only`, …), and the exact remediation command. No more empty docs pages in the UI without context (#173).
- **Workspace-aware web UI.** Sidebar now shows every workspace repo including unindexed ones (rendered as disabled `needs index` / `missing` rows linking to the Workspace dashboard). Workspace dashboard has a top-level **Sync workspace** button plus per-repo **Sync** / **Index now** actions wired to the new `POST /api/workspace/sync` endpoint. `RepoCard` surfaces `docs_skip_reason` under each card's stats (#173).
- **Per-repo search scope toggle** on `/repos/<id>/search` — switch between *this repo* and *workspace*. Synthetic `ws:<alias>` IDs automatically fall back to workspace scope (#173).
- **`/api/workspace/sync`** endpoint fans out the existing job executor across every workspace repo (or a single one with `repo_alias`). Returns one `{alias, repo_id, status, reason}` per repo so the UI can render granular feedback (#173).
- **`/api/search` accepts a `repo_id` query param** in workspace mode. Returns `[]` for synthetic `ws:<alias>` IDs (the corresponding repo isn't indexed) and fans out across every loaded FTS / vector store when omitted (#173).
- **`/api/repos` returns workspace metadata per row** — `workspace_alias`, `workspace_status` (`indexed` | `needs_index` | `missing_dir`), `is_primary`, `docs_enabled`, `docs_skip_reason`. Unindexed entries appear as synthetic rows with `id="ws:<alias>"` so frontends can render a "Needs index" CTA card instead of silently dropping them (#173).
- **Shiki syntax highlighting** in wiki page code blocks — client-side, lazy-loaded with the Vesper theme, falls back to plain text on failure (#171).
- **Centrality Leaderboard right-rail** on the graph view (PageRank / Betweenness / Degree) and **Hot Symbols Board** with score-driven intensity bars on the symbols table (#171).

### Changed
- **Dependency heatmap rewritten** from canvas to CSS Grid — adds hover tooltips, row/column highlighting, a legend, an `external:`-prefix stripper for `displayLabel`, and caps the rendered grid at the 15 most-connected modules (#171).
- **Docs filter panel defaults to expanded** on first render of the Docs page (#171).
- **MCP server is workspace-aware** end-to-end. `get_overview(repo="all")` returns a workspace summary with cross-repo topology; `search_codebase(repo="all")` runs Reciprocal Rank Fusion across every repo; tools that can't meaningfully fan out return `_unsupported_repo_all()` with the available aliases. (Pre-existing scaffolding; this release adds tests + audit confirmation.) (#173)

### Fixed
- **`repowise update` from a workspace root no longer errors with "No previous sync found".** Auto-detection routes the command to workspace mode and prints `[workspace] running across N repos`; the helper performs all detection before `ensure_repowise_dir` is called, so stray `.repowise/` directories no longer get created at the workspace root. Original Discord report that motivated the overhaul (#173).
- **`repowise serve` in workspace mode no longer drops unindexed repos from the sidebar.** Server lifespan now builds `app.state.workspace_fts: dict[repo_id, FullTextSearch]` (per-repo, includes the primary) and lazily rehydrates each workspace LanceDB store via `resolve_workspace_vector_store()` with an `asyncio.Lock` per repo so concurrent searches don't double-open. Reuses the primary store's embedder so workspaces built with gemini/openai stay embedding-compatible across fan-out (#173).
- **`.repowise-workspace.yaml` no longer drifts when a child repo is updated outside the orchestrator.** New `sync_workspace_state_from_disk()` reads each repo's `state.json` at the start of every `update_workspace` and refreshes `last_commit_at_index` so workspace-level decisions never operate on stale info (#173).

### Documentation
- **CLI Reference rewritten** for workspace mode — new cross-cutting auto-detect section, per-command flag tables updated for `update`, `watch`, `search`, `status`, `dead-code`, `costs`, `workspace add`, `doctor` (#173).

### Dependencies
- **`shiki` ^4.0.0** added as a `packages/ui` dependency for client-side wiki code highlighting (#171).

---

## [0.7.1] — 2026-05-10

### Fixed
- **`repowise serve` 404 on the web tarball for v0.7.0.** The v0.7.0 publish workflow failed during the web build: `useSearchParams()` inside the new `ContextDrawerShell` (mounted in the root layout for the `?drawer=` URL sync added in #168) tripped Next.js' static prerender of `/settings` with a missing-Suspense bailout. The Python wheel published to PyPI but no `repowise-web.tar.gz` was attached to the v0.7.0 GitHub release, so end-user `repowise serve` falls through to "API only". v0.7.1 wraps `ContextDrawerShell` in a `<Suspense>` boundary in `packages/web/src/app/layout.tsx` so the layout no longer blocks static prerendering. **Anyone who installed 0.7.0 should upgrade to 0.7.1** to get a working web UI from `repowise serve`.

---

## [0.7.0] — 2026-05-10

### Added
- **Risk page (consolidated).** New `/repos/<id>/risk` route brings the Heatmap, Hotspots, Dead Code, and Impact views under a single page with a persistent summary strip across the top. Hotspot rows are now clickable and open the universal File Card (#168).
- **Security page.** New `/repos/<id>/security` route renders severity distribution, findings-by-directory, and a clickable findings table over the existing security signals (#168).
- **Costs reorganization.** `/repos/<id>/costs` now splits into five tabs — Daily, Cache, Hotspots, Providers, Operations — backed by new `cache-hit-ratio-card`, `cost-heatmap`, `operation-breakdown`, and `provider-comparison` components (#168).
- **Universal File Card.** New `FileCard` + `FileCardDialog` (`@repowise-dev/ui/shared/file-card`) shows a unified overview of any file — git signals, docs, symbols, dead-code findings, decisions, security issues — with sections that render only when the underlying data exists. Wired into Risk and Symbols (#168).
- **Hot Symbols Board** with score-driven intensity bars over the symbols table; **Centrality Leaderboard** as a collapsible right-rail panel on the graph view (PageRank / Betweenness / Degree) (#168).
- **Docs onboarding.** `FirstFiveFiles` "Start here" card on the Docs page (collapsed by default) links to `/docs?page=<id>`. New `DriftBanner` and `ConfidenceVsFreshnessMatrix` on docs/coverage. Mermaid diagrams now have a maximize button with a zoom/pan modal and a neutral brand theme (#168).
- **Cross-page surfacing.** Shared `RelatedAcrossRepowise` collapsible footer plus new `EntityLink` / `EntityHoverCard` primitives and a `ContextDrawer` scaffold with URL sync (#168).
- **`packages/ui` exports.** New entry points: `./costs`, `./security`, `./onboarding`, `./shared/file-card`, `./shared/related` (#168).

### Fixed
- **SymbolDrawer right-rail text cutoff** — drawer widened and `ScrollArea` padding adjusted (#168).
- **Risk Hotspots table overflow** — long rows now truncate cleanly and open the File Card on click instead of pushing the layout (#168).
- **Health-score ring** skipped doc components for index-only repos so the score reflects what was actually computed (#168).
- **Chat `ToolCallBlock` hydration error** — split a button nested inside another button into sibling elements (#168).
- **Docs explorer sidebar toggle** anchored to its `relative` parent instead of falling back to the viewport, so it no longer overlaps the header (#168).
- **Docs "Start here" links** now route to `/docs?page=<id>` instead of broken wiki slugs (#168).

---

## [0.6.2] — 2026-05-10

### Fixed
- **Dead-code analyzer flagged DI-injected and convention-loaded code as unused.** On real .NET solutions (e.g. eShop) the analyzer surfaced ~1,350 false positives — gRPC services, EF `DbContext`s, MAUI entry points, mock services bound through `AddSingleton<TService, TImpl>()`, and most public interfaces. Three classes of fix landed: (a) `_NON_IMPORTABLE_SYMBOL_KINDS` now skips `method`, `variable`, `field`, `property`, `enum_member`, `constant`, `type_alias`, `namespace`, `module`, and `interface` from the unused-export pass — these aren't importable by name in any language, so absence of an `imports` edge isn't evidence of unreachability. (b) The `.NET` dynamic-hint extractor (`packages/core/src/repowise/core/ingestion/dynamic_hints/dotnet.py`) now matches the full DI surface: `Add|Map|Use` × `Scoped|Singleton|Transient|HostedService|DbContext(Pool|Factory)?|HttpClient|Options|GrpcService|GrpcClient|Hub|SignalR|Controllers?|Middleware`, plus `Configure<T>`, integration-event subscriptions, and class-name collision via a `type → list[file]` map so two classes named `BasketService` in different microservices both receive the synthetic edge. (c) `_detect_zombie_packages` now skips dot-dirs and code-less directories. eShop dead-code findings dropped 2,483 → 459 (−81%); safe-to-delete 1,354 → 339 (−75%) (#164, #166).
- **Symbol-level PageRank and betweenness were always 0.** Centrality only ran on the file subgraph, so the symbol detail panel showed 0 for every symbol regardless of how heavily it was called or referenced. `GraphBuilder` now exposes `symbol_subgraph()` (calls + heritage edges between symbol nodes) plus `symbol_pagerank()` and `symbol_betweenness_centrality()` with caches; `compute_metrics_parallel()` includes them; `persist_graph_nodes()` writes them to `graph_nodes.pagerank` / `betweenness` for symbol rows. On the local repo: 0/3,747 → 3,753/3,753 symbols with non-zero centrality (#164).
- **CLAUDE.md tech-stack inferred Node.js for any repo with a `package.json`.** A `package.json` containing only dev dependencies (Prettier, ESLint, Husky) was enough to brand a Python or .NET repo as "Node.js". Detection is now gated on real runtime evidence (`runtime_deps`, `main`/`bin`/`module`/`exports`/`engines.node`, or a framework dep). Added .NET / ASP.NET Core / EF Core / Aspire / gRPC / MAUI detection from `.csproj` / `.sln` / `Directory.Build.props` (#164).
- **`repowise update --index-only` crashed with `NameError: cannot access free variable 'dead_code_report'`.** Pre-existing bug: `dead_code_report` was defined inside the docs-generation branch but referenced after it. Moved dead-code analysis above the `if index_only:` early return; both index-only and full update paths now re-persist `graph_nodes` so symbol metrics stay current on incremental refresh (#164).
- **`persist_pipeline_result` raised `NameError: name 'nodes' is not defined`** in CI integration tests after the persistence refactor extracted `persist_graph_nodes`. The final `logger.info` summary still referenced `len(nodes)` from the removed loop. Now reads node count from the graph builder (#166).
- **C# entry-point detection missed MAUI / WPF / WinUI starts.** `MauiProgram.cs`, `Main.cs`, and `App.xaml.cs` are now recognised entry points alongside `Program.cs` (#164).
- **Embedding latency serialised LLM throughput in `PageGenerator.generate_all()`.** The page-generation semaphore was held while `embed_and_upsert()` ran, so a slow vector-store endpoint reduced effective generation concurrency to whatever embedding could keep up with. The LLM semaphore is now released as soon as a page is generated and embedding runs behind a separate `embed_concurrency` semaphore (defaults to `max_concurrency`). New `GenerationConfig.embed_concurrency` field (#163).

### Added
- **`AUDIT_NOTES.md`** at the repo root tracks deferred proper fixes from the May 2026 .NET audit (constructor-parameter type-use edges, XAML/Razor binding-path resolution, minimal-API extension-method resolution, member-access "uses" edges, co-change pair extraction returning 0 on real repos, hotspot ranking using `temporal_hotspot_score`, symbol metrics in `get_context`, language-aware never-flag patterns, graph-driven tech-stack inference, narrowing the `kind=interface` skip once ctor-param edges land). Each item has root cause, proper fix, touch points, and an estimate so future sessions can pick them up cold (#166).

### Changed
- **`repowise serve` rebuilds the local web bundle when source is newer.** Previously `serve` would launch the cached UI even after `git pull` had updated `packages/web/`. Now compares mtimes and rebuilds when stale; new `--refresh-ui` flag forces a rebuild. Affects local-monorepo dev only — end-user installs continue to download `repowise-web.tar.gz` matched to the wheel version (#165).
- **Smarter Claude Code augment hook.** PostToolUse enrichment now runs against `Bash`/`Edit`/`Write` only and skips noisy `Grep`/`Glob` PreToolUse, with self-healing migration for legacy hook entries on upgrade (#162).

---

## [0.6.1] — 2026-05-10

### Added
- **DeepSeek provider** — `deepseek-v4-flash` (default) and `deepseek-v4-pro` are now first-class LLM providers via DeepSeek's OpenAI-compatible API at `api.deepseek.com`. Implementation mirrors the OpenRouter pattern (openai SDK + custom `base_url`), with `generate()` and `stream_chat()` (incl. tool calling), 3-attempt exponential-backoff retries on rate limits, dedicated rate-limit defaults (60 RPM / 200K TPM), per-model pricing in the cost tracker, and full plumbing through CLI provider resolution, MCP `get_answer` auto-detection, the run-config form, and the settings-page provider list. New env vars: `DEEPSEEK_API_KEY` (required), `DEEPSEEK_BASE_URL` (optional override) (#159).

### Fixed
- **Claude Code hook crashes when the active venv is broken.** PreToolUse (`Grep`/`Glob`) and PostToolUse (`Bash`) hooks invoked the full `repowise augment` Click command, whose import chain pulls `cli.main` → `init_cmd` → `cost_estimator` → `core.ingestion.graph` → `networkx`/`scipy`. A single missing dependency in the user's environment caused every tool call to surface an `ImportError` traceback and non-zero exit, because the in-handler `try/except` could not catch failures during module loading. Hooks are now wired to a new `repowise-augment` console script (`repowise.cli.augment_hook:main`) that imports only the augment handler — module-level imports are stdlib-only — and wraps the entire run, including the lazy import of the handler, in a last-ditch `except BaseException` so any failure exits 0 silently. Existing users upgrading from any prior version are migrated automatically: every `repowise <command>` invocation, plus the hook itself on first firing, idempotently rewrites legacy `repowise augment` entries in `~/.claude/settings.json` to `repowise-augment` — `pip install -U repowise` is the only step needed (#160).
- **`repowise mcp` couldn't reach the LLM that `init` had configured.** The MCP server didn't load `.repowise/.env` at startup, so `get_answer` fell back to retrieval-only with `confidence=low` even when `init` had completed cleanly with a real provider. The resolver now reads `state.json` (provider + model) and `.env` (API keys) as a fallback layer behind process env, and the `mcp` command itself loads `.repowise/.env` on startup. Same `repowise init` configuration is now reused end-to-end without re-exporting anything (#158, #159).
- **`get_overview` crash on legacy databases.** The repo-overview query used `scalar_one_or_none()` while older indexes left a stale `target_path="repo"` row alongside the canonical `target_path=<repo_name>` row, raising `MultipleResultsFound` on the documented "best first call". Switched to a deterministic ordered `.first()`: prefer the row matching the repository name, fall back to most recently updated. Same fix in the workspace overview path (#158).
- **`get_why` routing natural-language questions to `mode=path`.** The `_is_path` heuristic returned True for any query containing `/`, so questions like *"why does init use a two-phase plan/apply flow"* dispatched to the path branch and returned empty results. Heuristic now recognises NL up front (trailing `?`, leading question word, or 4+ tokens including a question word route to search; whitespace anywhere disqualifies a path); genuine paths like `src/auth/service.py` still route to `mode=path` (#158).
- **README marker examples leaking into decision records.** The inline-marker scanner walked the whole tree, including `repowise.egg-info/PKG-INFO`, where setuptools embeds `README.md` verbatim — so example `# WHY:` / `# DECISION:` / `# TRADEOFF:` lines from the README surfaced as real architectural decisions in `get_why`'s health dashboard. Walker now excludes `*.egg-info` and `*.dist-info` (#158).
- **`.env` parser handled only the simplest format.** `load_dotenv` now correctly handles `export KEY=value`, single- and double-quoted values, and inline `# comments`, fixing a common 401 cause where quoted API keys were treated literally and where `export`-prefixed entries were silently ignored (#159).
- **Provider import / `get_provider` failures logged at debug level.** A user-visible failure (no provider available for `get_answer` to synthesize an answer) was hidden in debug logs; now logged at `warning` so the cause is discoverable without debug logging enabled (#159).

### Changed
- **`gemini` re-added to the run-config form** provider list — was inadvertently dropped in the v0.6.0 frontend reshuffle (#159).
- **`litellm` API-key resolution** in CLI provider plumbing — `LITELLM_API_KEY` is now picked up alongside the existing `LITELLM_BASE_URL` / `LITELLM_API_BASE` (#159).

---

## [0.6.0] — 2026-05-09

### Added
- **Sigma.js graph renderer** replaces React Flow as the primary graph view. ForceAtlas2 web-worker layout for the `force` mode and ELK-driven hierarchical layout share a single canvas. Inspection panel, search, community dimming, execution-flow highlighting, and legend counts all reach parity via Graphology adapters; double-click drills into modules and rebuilds the graph with child file nodes inline. Signal overlays (dead-code desaturation, hotspot tint, entry-point size boost) live in the Sigma `nodeReducer` (#148).
- **Per-phase progress for graph build and metrics** — `GraphBuilder.build()` now reports imports/heritage/calls as sub-phases through the existing `ProgressCallback`, and the orchestrator drives metrics, communities, and flows as their own sub-phases by priming the lazy caches. `repowise init` now shows six indented bars under the graph phase instead of a single opaque spinner that previously sat at "0/1" for 5–10 minutes (#150).
- **Dashboard `EmptyState` guards** — every dashboard panel now renders a labelled empty state instead of going blank when its data slice is missing (#148).

### Changed
- **`repowise update` defaults to the mode `init` was run with.** `repowise init` now persists `docs_enabled` to `.repowise/state.json` (true for full init, false for `--index-only`), and `repowise update` reads that field so the post-commit hook does the right thing without extra knobs. New `--docs/--no-docs` flags override per run; `--index-only` still wins. Index-only init now also writes `state.json`, so the post-commit hook has a baseline to diff against (#155).
- **Cost-gate persistence.** Declining the cost gate now produces a clean index-only outcome instead of an aborted half-state — ingestion, graph, git, and dead-code work is persisted, `state.json` lands with `docs_enabled=False`, and subsequent `repowise update` runs default to index-only so there are no surprise LLM charges later (#156).
- **Cost-gate prompt** is now visually separated from the Rich progress output above it (blank line + horizontal rule before the `[y/N]`), preventing the prompt from being missed mid-output (#156).
- **Stale-wiki warning is much quieter.** The `repowise augment` PostToolUse hook used to fire on every Bash tool call after a commit until an update completed; now it suppresses while `repowise update` holds `.repowise/.update.lock`, and after warning once for a given HEAD it skips further warnings until HEAD moves. The hook installer also detects and excises legacy non-marker bodies before appending the marker block (#155).

### Fixed
- **Python relative imports drop their first imported name.** `from .X import Y` and `from .X import A, B, C` were being parsed with `Y`/`A` discarded because the binding extractor's "skip the first dotted_name" heuristic, correct for absolute `from foo.bar import X`, also fired on tree-sitter's `relative_import` wrapper. The graph stored `imported_names_json: []` for affected edges, which propagated into massive dead-code false positives on Repowise's own source (e.g. `GraphBuilder`, `DeadCodeAnalyzer`, `CallResolver` flagged at confidence 1.0). The extractor now detects `relative_import` and skips the heuristic, with regression coverage for both relative and absolute shapes (#149).
- **Dead-code unused-export false positives.** Symbol decorators are now persisted on graph nodes (the framework-decorator whitelist was previously running against an empty list), `@`-prefixed decorator names are matched against the bare prefixes in `_FRAMEWORK_DECORATORS` (so `@router.get`, `@asynccontextmanager`, etc. are recognised), and nested function definitions are skipped from unused-export detection — closures and inner generators can't be imported by name and were being flagged spuriously (#153).
- **State migration for legacy index-only installs.** `state.json` files written before #155 lack `docs_enabled`. The previous default would have charged a full LLM regen on the first upgrade-and-commit for users who had originally run `init --index-only`. The resolver now infers `docs_enabled=False` when `provider`/`model` are also absent (the legacy shape of an index-only state file), backfills the explicit value into `state.json` on first update, and preserves the existing default for full-init users (#156).
- **Workspace-mode chat 404.** `POST /api/repos/{repo_id}/chat/messages` was the only `/api/repos/{repo_id}/...` endpoint not honouring `app.state.workspace_sessions`, so every non-primary repo's chat returned `404 Repository <id> not found` despite appearing in `GET /api/repos`. Factory-resolution logic is now lifted into `resolve_session_factory` / `resolve_request_session_factory` helpers in `deps.py`, the chat router uses the request-scoped helper, and the duplicate helper in `routers/repos.py` is now a one-line alias (#146).
- **Init progress rendering** cleanup — phase labels and indentation alignment fixes (#151).

### Performance
- **Graph metrics fan out in parallel.** PageRank, betweenness, file/symbol community detection, and execution-flow tracing previously ran serially across persist + generation, with PageRank and betweenness recomputing from scratch on each call. `GraphBuilder` now caches all four kernels on the instance (invalidated on `build()`), and a new `compute_metrics_parallel()` runs them via `asyncio.gather` + `asyncio.to_thread` so subsequent lazy callers hit warm caches. Betweenness dominates worst-case wall-time (O(VE)); fanning it out alongside PageRank and community detection meaningfully shortens the metrics phase. Falls back to lazy computation if `compute_metrics_parallel()` is never called (#152).
- **Tree-sitter query cache promoted to module-level `@lru_cache`** keyed by language tag. Process-pool parse workers each held their own per-instance cache and recompiled every `.scm` query on first use; now each worker compiles each grammar's query exactly once for its lifetime (#154).
- **Per-file `Compiled query language=...` debug log dropped.** It fired once per parser-instance × language during ingestion and was the single noisiest source of unfiltered stdout during `repowise init` (#149).

### Dependencies
- `gitpython` 3.1.47 → 3.1.50 — security release: rejects out-of-repo reference manipulation (3.1.48) and rejects control characters in config writes (3.1.49) (#147).

---

## [0.5.1] — 2026-05-07

### Added
- **TYPO3 framework edges** — composer-based extension discovery (`"type": "typo3-cms-extension"`, canonical for v11–v14) with legacy `ext_emconf.php` fallback and project-mode `vendor/<vendor>/<package>/` walking. Convention-loaded files (`ext_localconf.php`, `ext_tables.php`/`.sql`, `Configuration/TCA/*.php`, `Configuration/TCA/Overrides/*.php`, `Configuration/Backend/*.php`, `Configuration/Services.{php,yaml,yml}`, `JavaScriptModules.php`, `ContentSecurityPolicies.php`, `RequestMiddlewares.php`, `Icons.php`, `RTE/*.{yaml,yml}`) now receive incoming edges from a synthetic `framework:typo3-core` anchor and are no longer flagged as unreachable. `Configuration/JavaScriptModules.php` is parsed for `EXT:<key>/...js` references and edges are added to the registered JS modules. `tech_stack.detect_tech_stack` recognises `typo3/cms-core`, `symfony/framework-bundle`, and `laravel/framework` from `composer.json` (#114).
- **`framework:` synthetic-node prefix in dead-code analysis** — distinguishes framework-mediated wiring from third-party `external:` imports. `framework:` predecessors count as cross-package importers (preventing legitimate convention dirs like `Configuration/` from showing as zombie packages); `external:` predecessors do not (#114).

### Fixed
- **`repowise dead-code` now invokes `add_framework_edges`** — the CLI previously skipped framework-aware edge synthesis, so even Django/Laravel/Rails repos showed convention files as false-positive unreachable findings. The dead-code command now calls `detect_tech_stack` and adds framework edges before running the analyzer (#114).

### Dependencies
- `cryptography` 43.0.3 → 46.0.7 (#130).
- `lodash` 4.17.23 → 4.18.1 (#131).
- `lodash-es` and `langium` transitive bumps (#129).
- `esbuild`, `vitest`, and `vite` dev tooling bumps (#134).

---

## [0.5.0] — 2026-05-03

### Changed
- **Build packaging hardened** — `pyproject.toml` now uses `[tool.setuptools.packages.find]` to auto-discover all `repowise.*` subpackages across `packages/{core,cli,server}/src`, replacing the hand-maintained explicit list. Eliminates the missing-subpackage drift class that previously required hotfixes (#97, #110, #115).
- **Frontend monorepo restructure** — visualization, dashboard, chat, wiki, graph, and workspace components extracted from `packages/web` into shared `@repowise-dev/ui` and `@repowise-dev/types` workspace packages (~50 components). Fully transparent to `pip install repowise` users — the published `repowise-web.tar.gz` standalone bundle is unchanged in shape and behaviour. OSS contributors benefit from clearer module boundaries; both packages resolve via npm workspace symlinks with no extra auth required.
- **`packages/web` declares its workspace dependencies explicitly** — `@repowise-dev/ui` and `@repowise-dev/types` are now listed in `packages/web/package.json` so isolated installs (`cd packages/web && npm install`) no longer fail with module-not-found.

### Fixed
- **Jobs reliability pass** — cancel endpoint added; progress hydration covers all phases; stuck-job detection on startup resets stale `pending`/`running` rows; SQLite WAL contention reduced during sync; per-repo DB used in workspace mode (#117).
- **`repowise update` now persists LLM costs** — costs were being computed but not written to the `llm_costs` table during incremental updates; cost dashboards underreported spend (#108).
- **Workspace dashboard** — contract summary now renders when contracts exist but no cross-repo links have been detected, instead of showing an empty state (#111).

### Documentation
- **Computed glossary** — `docs/COMPUTED_GLOSSARY.md` documents every derived metric, score, and signal Repowise computes (PageRank, hotspot score, freshness, confidence tiers, etc.) so the surface vocabulary is discoverable in one place (#127).
- **README + UI/UX audit fixes** — confirmation dialogs, mobile responsiveness, accessibility, and empty/error/loading states across the dashboard (#117).

### Dependencies
- `python-dotenv` 1.0.1 → 1.2.2 (#98).

---

## [0.4.1] — 2026-04-30

### Fixed
- **Wheel packaging** — `pyproject.toml` `[tool.setuptools] packages` list extended to include subpackages omitted in 0.4.0; some installs were missing modules at runtime (#110).
- **`get_answer` MCP tool** — citation format and confidence gating fixes (#107).

---

## [0.4.0] — 2026-04-26

### Added

#### C# Full tier
- **MSBuild-aware import resolver** — new `resolvers/dotnet/` subpackage parses every `.csproj` and `.sln` in the repo, builds a namespace → file map across projects, walks `Directory.Build.props` ancestry, and resolves `using` directives by ranking candidates: same project → directly-referenced project → anywhere. NuGet `<PackageReference>` ids are emitted as `external:nuget:<id>` nodes. Falls back to legacy stem-match for repos without `.csproj`.
- **Modern C# language features** — `csharp.scm` now captures `record_declaration`, `delegate_declaration`, `event_declaration`/`event_field_declaration`, `field_declaration`, `enum_member_declaration`, and both block-form and file-scoped `namespace_declaration`. `LANGUAGE_CONFIGS` and the registry's `heritage_node_types` are extended accordingly.
- **`global using` / `using static` / `using alias` propagation** — `NamedBinding` gains `is_global` and `is_static_import` flags; `extract_csharp_bindings` distinguishes all four flavours of `using` directive. Default `<ImplicitUsings>` set (with Web SDK extras) and `global using` lines are merged into a per-project implicit-usings set used by the resolver.
- **XML doc parsing** — module-level and symbol-level `///` runs are extracted, `<summary>` content is unwrapped as the rendered docstring, structural tags (`<param>`, `<returns>`, `<see/>`) are stripped, and `<inheritdoc/>` emits a `{inheritdoc}` marker.
- **Heritage for records** — `record User(...) : Base(args), IInterface` now produces both `extends` and `implements` edges; primary-constructor argument lists are skipped.
- **ASP.NET / .NET framework edges** — `_add_aspnet_edges()` runs whenever the tech stack mentions ASP.NET or any `.cs` file imports `Microsoft.AspNetCore.*`. Adds edges from `Program.cs` / `Startup.cs` to every `[ApiController]` file, `app.MapGet/...` handler classes, `app.UseMiddleware<T>()` middleware, and from each `DbContext` to entity files referenced via `DbSet<T>`.
- **.NET dynamic hints** — new `DotNetDynamicHints` extractor (registered in `HintRegistry`) records DI registrations (`AddScoped`/`AddSingleton`/`AddTransient`/`AddHostedService`), reflection (`Activator.CreateInstance`, `Type.GetType`, `Assembly.Load*`), `[assembly: InternalsVisibleTo]`, and MEF `[Export]`/`[ImportMany]` as graph edges.
- **Workspace contract extraction for ASP.NET and gRPC-dotnet** — `http_extractor.py` learns `[HttpGet/Post/...]` attribute routing with class-level `[Route]` prefix stitching, parameterless `[HttpVerb]` attributes, minimal API (`app.MapGet`/...), and HttpClient consumers (`*Async`). `grpc_extractor.py` recognises `app.MapGrpcService<T>()`, `class X : Service.ServiceBase`, and `new ServiceClient(channel)`.
- **Cross-repo `<ProjectReference>` and internal NuGet** — `cross_repo._scan_csproj` walks every `.csproj` in every workspace repo and emits `dotnet_project_ref` for cross-repo project references and `dotnet_nuget_internal` when a `<PackageReference>` id matches a sibling repo's `<AssemblyName>`.
- **Dead-code dynamic markers for C#** — `_DYNAMIC_IMPORT_MARKERS` learns reflection / DI / MEF / `InternalsVisibleTo` patterns so the dead-code analyser doesn't flag types only loaded by the framework at runtime.
- **Multi-project test fixtures** — `tests/fixtures/dotnet_solution/` (Api / Domain / Infrastructure with EF Core, controllers, minimal API, GlobalUsings) and `tests/fixtures/dotnet_workspace/` (3 repos demonstrating cross-repo `<ProjectReference>` + internal-NuGet patterns), with end-to-end coverage in `tests/integration/test_dotnet_solution.py`.

#### Dead-code accuracy
- **Dynamic-edge consumption in dead-code analysis** — graph edges of type `dynamic` / `dynamic_*` (emitted by every dynamic-hint extractor) now suppress dead-code findings automatically. `find_dynamic_edge_files()` enumerates files involved in those edges and unions the result with the existing source-text `_DYNAMIC_IMPORT_MARKERS` scan. Sub-types (`dynamic_uses`, `dynamic_imports`) are preserved on the graph edge instead of being squashed.
- **Per-language dynamic-import markers** — `_DYNAMIC_IMPORT_MARKERS` extends to Go (`reflect.TypeOf`/`reflect.ValueOf`), Ruby (`Object.send`, `Kernel.const_get`, `.public_send`), PHP (`call_user_func*`, `new $class`, `ReflectionClass`), Kotlin (`KClass.createInstance`, `::class.java`), Swift (`NSClassFromString`, `Selector`, `#selector`, `NSStringFromClass`), and Scala (`Class.forName`, `runtimeMirror`, `reflect.runtime`).
- **`detect_unused_internals` enabled by default** — private-symbol findings now surface in the standard dead-code report at confidence 0.65 with `safe_to_delete=False`. CLI defaults stay explicit-False so `repowise dead-code` is unchanged unless `--include-internals` is passed.

#### Workspace-aware resolvers across the Good tier
- **PHP composer PSR-4** — `resolvers/php_composer.py` reads `autoload.psr-4` and `autoload-dev.psr-4` from `composer.json`, builds a longest-prefix-wins namespace → directory map, and is consulted before stem fallback. Real Laravel/Symfony apps with `"App\\": "src/"` style maps now resolve.
- **Go multi-module monorepos** — `resolve_go_import` walks every `go.mod` in the repo (skipping `vendor`/`node_modules`), records `(module_dir, module_path)` tuples on the resolver context, and matches imports by longest module prefix. Single-module back-compat preserved.
- **TypeScript SFC + workspace package resolution** — `.vue`, `.svelte`, and `.astro` extensions probed only when the repo actually contains SFC files. npm/yarn/pnpm `workspaces` (array or object form, with glob expansion) are parsed from root `package.json` so `@scope/pkg` and `@scope/pkg/sub/path` resolve to the sibling workspace dir before falling back to `external:`.
- **Kotlin Gradle subprojects** — `resolvers/kotlin_gradle.py` parses `settings.gradle(.kts)` `include(...)` declarations plus per-module `srcDirs(...)` overrides (defaults `src/main/kotlin`, `src/main/java`), then walks each source root recording `package` declarations into a `package_to_files` map.
- **Ruby Rails / Zeitwerk autoloading** — gated on `config/application.rb`, `resolvers/ruby_rails.py` builds bare-name and namespaced-name maps over standard autoload roots (`app/*`, `lib/`). `ResolverContext.rails_lookup` exposes the index for callers (heritage, call resolution, framework edges).
- **Swift SPM target → directory mapping** — `resolvers/swift_spm.py` regex-parses `.target(name: "X", path: "Y")`, `.executableTarget`, and `.testTarget` declarations across all `Package.swift` files in the repo (defaults `Sources/<Name>` for code, `Tests/<Name>` for tests).
- **Scala SBT / Mill multi-project** — `resolvers/scala_build.py` autodetects the build tool (`build.sbt` vs `build.sc`) and parses subprojects (SBT `lazy val core = project.in(file("core"))`, Mill `object Foo extends ScalaModule`). Walks each project's `src/main/scala` (or `src/`) recording packages into `package_to_files`.
- **Cargo workspace crate resolution** — `resolvers/rust_workspace.py` parses root `Cargo.toml` `[workspace] members = [...]` plus each member's `[package] name`. `resolve_rust_import` consults the index after the same-crate probe so `use sibling_crate::module` resolves to the sibling crate's `src/`. Cargo's `-` → `_` import-identifier rewrite is honoured.

#### Framework-aware edges (every major web framework)
- **Spring Boot (Java/Kotlin)** — `@Component`/`@Service`/`@Repository`/`@Controller`/`@RestController`/`@Configuration` bean classes wire to their injection sites via `@Autowired` field/constructor analysis. Interface-typed dependencies fall back to `parsed.heritage` to find implementing classes. `@Bean` factory methods in `@Configuration` classes link to their return-type files.
- **Rails (Ruby)** — `config/routes.rb` is line-walked with namespace-stack tracking: `resources :users`, `get "/foo", to: "users#index"`, and nested `namespace :admin do … end` all resolve to controller files via the Zeitwerk autoload index. ActiveRecord `belongs_to`/`has_many`/`has_one` relationships link model files (with simple inflector-style singularisation).
- **Laravel (PHP)** — `routes/web.php` and `routes/api.php` parse modern `[Foo::class, 'method']` and legacy `'Foo@method'` syntaxes, plus `Route::resource`. Service-provider `bind`/`singleton`/`instance` calls link providers to bound classes. Eloquent `hasMany`/`belongsTo`/`hasOne` link models. Class resolution uses the composer PSR-4 map first, falling back to stem.
- **TYPO3 (PHP)** — extension discovery via `composer.json` `"type": "typo3-cms-extension"` (canonical for v11–v14) with legacy fallback to `ext_emconf.php`; project-mode (`vendor/<vendor>/<pkg>/composer.json`) is also walked. Convention-loaded files (`ext_localconf.php`, `ext_emconf.php`, `ext_tables.sql`, `Configuration/TCA/*.php`, `Configuration/Backend/*.php`, `Configuration/JavaScriptModules.php`, `Configuration/ContentSecurityPolicies.php`, `Configuration/RequestMiddlewares.php`, `Configuration/Services.php`, `Configuration/Icons.php`) get incoming edges from a synthetic `framework:typo3-core` anchor, so they are no longer flagged as unreachable. `Configuration/JavaScriptModules.php` is parsed for `EXT:<key>/...js` references and edges are added to the registered JS modules. `tech_stack.detect_tech_stack` recognises `typo3/cms-core` and `symfony/framework-bundle` / `laravel/framework` from `composer.json`.
- **`framework:` synthetic-node prefix in dead-code analysis** — distinguishes framework-mediated wiring from third-party `external:` imports. `framework:` predecessors *do* count as cross-package importers (preventing legitimate convention dirs like `Configuration/` from showing up as zombie packages); `external:` predecessors do not.
- **`repowise dead-code` now invokes `add_framework_edges`** — the CLI previously skipped framework-aware edge synthesis, so even Django/Laravel/Rails repos showed false positives. The dead-code command now calls `detect_tech_stack` and adds framework edges before running the analyzer.
- **Express / NestJS (TS/JS)** — Express `app.use(routerVar)` mirrors the FastAPI router-var pattern (resolves imported names ending in `Router`/`router` to source file). NestJS `@Module({ controllers: [...], providers: [...], imports: [...] })` arrays parse into module → target edges using a class-name → file map.
- **Gin / Echo / Chi (Go)** — `r.GET("/p", users.Index)` style handler references resolve via the Go import list (using the multi-module resolver) for package-qualified handlers, or via a function-name → file map for receiver methods. Lambda handlers are accepted as missed.
- **Axum / Actix (Rust)** — Axum `Router::new().route("/p", get(handler))`, Actix `web::resource("/p").route(web::get().to(handler))` / `.service(handler)` / `.configure(routes::register)` all resolve to handler files via a function-name → file map.

#### Per-language dynamic-hint extractors
- **Spring (JVM)** — `applicationContext.getBean(Foo.class)` and named-bean lookups, plus `@Bean` factory return-types.
- **Ruby** — `Object.send(:method)` / `.public_send`, `Kernel.const_get`, `define_method`, ActiveSupport `delegate :foo, to: :bar`.
- **PHP** — `call_user_func`/`call_user_func_array`, `new ReflectionClass(Foo::class)`, container `get`/`app`/`resolve`/`make` with `::class` arguments, `new $variable` instantiation markers.
- **Scala** — `Class.forName(...)`, `runtimeMirror` / `reflect.runtime` markers, named `given foo: Bar = ???` and `implicit val foo: Bar = ???` declarations.
- **Swift** — `NSClassFromString("Foo")`, `NSStringFromClass(Foo)`, `Selector("name")`, `#selector(name)`, KVC `value(forKey: "key")`.
- **C** — function-pointer assignment (`fp = some_function;` where the right-hand side is a known function name), `dlopen("./libfoo.so")`, `dlsym(handle, "name")`.
- **Luau** — `game:GetService("Name")`, `setmetatable(t, {__index = Other})`, `require(game.Service.Path)` markers.
- **Go** — `reflect.TypeOf(Foo{})`, `plugin.Open(...)`, `plugin.Lookup(...)`.

#### Symbol-extraction coverage
- **Java records** — `record Point(double x, double y) {}` now captured as a class-kind symbol with optional modifiers.
- **Kotlin** — `typealias Foo = Bar` and top-level / class-level `val`/`var` properties (locals inside function bodies remain excluded).
- **Scala 3** — `enum_definition`, `given_definition` (named givens), and `var_definition` are now captured. `class_definition` and `function_definition` also capture leading annotations (`@deprecated`, `@tailrec`).
- **Swift** — `subscript_declaration` captured as a method-kind symbol.
- **Ruby** — top-level / class-level constant assignments (`MAX_RETRIES = 3`).
- **PHP** — `const_declaration` and `property_declaration` (with or without explicit visibility) at both file and class scope.
- **C** — `typedef int MyInt;` and `typedef struct Foo Bar;` aliases now produce symbols.
- **Java class/interface/record annotations** — `(modifiers) @symbol.modifiers` capture extended to `class_declaration`, `interface_declaration`, and `record_declaration` so framework decorators surface in the symbol view.

#### Documentation extraction
- **Java module-level Javadoc** — `extract_module_docstring` gains a Java branch that picks up a leading `/** ... */` block before the package/import declarations.
- **Luau docstrings** — both `--[[ block comment ]]` and runs of `---` triple-dash lines are extracted at module and symbol scope.

### Fixed
- **Java interface inheritance** — `interface IFoo extends IBase` now produces a heritage relation; the extractor previously only recognised the `interfaces` field on `class_declaration` and missed `extends_interfaces` on `interface_declaration`.
- **Go struct embedding** — `type Foo struct { Base }` correctly emits a heritage edge from `Foo` to `Base`. The Go heritage extractor now traverses the `field_declaration_list` child when no `body` field is present (matches the actual tree-sitter-go grammar layout).
- **Swift `extension_declaration` heritage** — extension conformance declarations now contribute heritage relations (`extension_declaration` was missing from Swift's `heritage_node_types`).

### Changed
- **Language tier promotion** — C# moves from "Good" to "Full" in `README.md` and `docs/LANGUAGE_SUPPORT.md`. Eight languages now sit at Full tier (was: seven).
- **Heritage / bindings / dead-code internals refactored into per-language subpackages** — `extractors/heritage.py` and `extractors/bindings.py` (previously 600+ LOC monoliths) and `analysis/dead_code.py` are now subpackages with one file per language plus a re-export shim. Public API (`extract_heritage`, `extract_import_bindings`, `DeadCodeAnalyzer`, etc.) is unchanged.

### Tests
- **+90 unit tests** covering workspace-aware resolvers (PHP, Go, TypeScript, Swift, Kotlin, Scala, Ruby, Rust), framework-edge extraction (Spring, Rails, Laravel, Express/NestJS, Gin/Echo/Chi, Axum/Actix), per-language dynamic-hint extractors, and Java/Ruby/Scala/PHP/Go heritage + binding extractors.

---

## [0.3.1] — 2026-04-26

### Added
- **Output language for generated wiki content** (#99) — set `language: ru` (or any of `en`, `es`, `fr`, `de`, `zh`, `ja`, `ko`, `it`, `pt`, `nl`, `pl`, `tr`, `ar`, `hi`) in `.repowise/config.yaml` to have the LLM produce documentation in that language. Code, paths, and symbol names stay untranslated. Cache keys include the language so different output languages do not collide. Closes #64.
- **Luau / Roblox language support** (#89) — promotes the existing git-blame-only `lua` LanguageSpec to a full AST-parsed `luau` tier covering both `.lua` and `.luau`. Includes a dedicated resolver for string-literal `require` plus `script.Parent` instance paths and the `:WaitForChild` / `:FindFirstChild` Rojo-safe idioms. Closes #52.
- **OpenRouter provider** (#56) — new `openrouter` LLM provider with full `stream_chat` plus tool-call support, plus an `OpenRouterEmbedder` defaulting to `google/gemini-embedding-001`. Sends OpenRouter's recommended `HTTP-Referer` and `X-Title` headers.
- **`base_url` plus per-provider env vars** (#85) — OpenAI, Anthropic, Gemini, Ollama, and LiteLLM all accept a `base_url` (with `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `OLLAMA_BASE_URL`, `LITELLM_BASE_URL` env fallbacks) so users can route requests through proxies and self-hosted OpenAI-compatible endpoints.

### Fixed
- **`database is locked` on concurrent `repowise update`** (#101) — every SQLite connection now opens with `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, and `foreign_keys=ON`. Two concurrent writers against the same workspace no longer collide; PostgreSQL is unchanged. Closes #95.
- **CLAUDE.md opt-out ignored in full mode** (#102) — the "Generate .claude/CLAUDE.md? [Y/n]" prompt was nested inside the advanced-config flow, so users in full mode were never asked and the writer always created the file. Prompt is now extracted into a standalone helper and asked in both modes. Closes #81.
- **`repowise init` could overwrite an unparseable user JSON config** (#94) — when `.mcp.json` or `.claude/settings.json` exists but is not valid JSON, init now aborts with a clear error instead of silently treating the file as empty and overwriting the user's contents.
- **Editable installs and CI builds were broken** (#97) — `[tool.setuptools].packages` referenced `repowise.core.ingestion.parsers` (no longer exists) and was missing `extractors`, `languages`, and `resolvers` (added during the language-support refactor). Resyncing the list unblocks `pip install -e .` and every PR's CI.
- **`repowise serve` pointed at the wrong GitHub release** — `_GITHUB_REPO` flipped from `RaghavChamadiya/repowise` to `repowise-dev/repowise` so the web UI tarball downloads from the correct release URL. Project URLs on PyPI updated to match.

### Changed
- **PreToolUse hook** — replaced FTS-only file retrieval with multi-signal ranking: symbol name match (highest weight), file path match, then FTS on wiki content. Returns top 3 files instead of 5. Removed git signals (HOTSPOT, bus-factor, owner) from enrichment output — use `get_risk` for that. Removed Bash command interception. Dependencies shown as "Uses" (2 per file) alongside symbols (3) and importers (3).
- **uv workflow documented and dev deps migrated to PEP 735** (#100) — README and USER_GUIDE document `uv tool install repowise` and `uv sync --all-packages`. Replaces the deprecated `[tool.uv] dev-dependencies` table with `[dependency-groups] dev`, silencing the `tool.uv.dev-dependencies is deprecated` warning every `uv pip install` was emitting.

### Security
- Bumps `dompurify` 3.3.3 → 3.4.1 (prototype-pollution + mXSS sanitizer-bypass fixes).
- Bumps `gitpython` 3.1.46 → 3.1.47 (argument injection via underscored kwargs).
- Bumps `mako` 1.3.10 → 1.3.11 (`TemplateLookup` path traversal).
- Bumps `litellm` 1.83.0 → 1.83.7 (routine patches).
- Bumps `python-multipart` 0.0.22 → 0.0.26 (case-insensitive headers, MIME info).

---

## [0.3.0] — 2026-04-13

### Added

#### Multi-repo workspaces
- **Workspace support** — `repowise init .` from a parent directory scans for git repos (up to 3 levels deep), prompts for selection, and indexes each repo with cross-repo analysis. Config stored in `.repowise-workspace.yaml`.
- **Workspace CLI commands** — `repowise workspace list`, `workspace add <path>`, `workspace remove <alias>`, `workspace scan`, `workspace set-default <alias>` for managing repos in a workspace.
- **Workspace-aware MCP server** — a single MCP server instance serves all workspace repos. Tools accept an optional `repo` parameter to target a specific repo or `"all"` to query across the workspace. Lazy-loading with LRU eviction (max 5 repos loaded simultaneously).
- **Cross-repo co-change detection** — analyzes git history across repos to find files that frequently change in the same time window.
- **API contract extraction** — scans for HTTP route handlers (Express, FastAPI, Spring, Go), gRPC service definitions, and message topic publishers/subscribers. Matches providers with consumers across repos.
- **Package dependency scanning** — reads package manifests (`package.json`, `pyproject.toml`, `go.mod`, `pom.xml`) to detect inter-repo package dependencies.
- **Workspace CLAUDE.md** — auto-generated context file at the workspace root covering all repos, their relationships, cross-repo signals, and contract links.
- **Workspace web UI** — workspace dashboard (`/workspace`) with aggregate stats and repo cards, contracts view (`/workspace/contracts`) with provider/consumer matching, and co-changes view (`/workspace/co-changes`) with cross-repo file pairs ranked by strength.
- **Workspace update** — `repowise update --workspace` updates all stale repos in parallel (up to 4 concurrent) then re-runs cross-repo analysis. `--repo <alias>` targets a single repo.
- **Workspace watch** — `repowise watch --workspace` auto-updates all workspace repos on file change.

#### Auto-sync hooks
- **`repowise hook` CLI** — `repowise hook install` installs a marker-delimited post-commit git hook that runs `repowise update` in the background after every commit. `hook install --workspace` installs for all workspace repos. `hook status` and `hook uninstall` for management.
- **Proactive context enrichment via Claude Code hooks** — `repowise init` registers PreToolUse and PostToolUse hooks in `~/.claude/settings.json`. PreToolUse enriches every `Grep`/`Glob` call with graph context (importers, dependencies, symbols, git signals) at ~24ms latency. PostToolUse detects git commits and notifies the agent when the wiki is stale.
- **Polling scheduler** — when the server is running, a background job polls registered repositories every 15 minutes and triggers updates for new commits missed by webhooks.

#### Graph intelligence
- **Symbol-level dependency graph** — the dependency graph is now two-tier: file nodes for module-level relationships and symbol nodes (functions, classes, methods) for fine-grained call resolution. Call edges carry confidence scores (0.0–1.0).
- **3-tier call resolution** — Tier 1: same-file targets (confidence 0.95). Tier 2: import-scoped targets via named bindings (0.85–0.93). Tier 3: global unique match (0.50). Extracted by tree-sitter for Python, TypeScript, JavaScript, Go, Rust, Java, and C++.
- **Named binding resolution** — tracks import aliases, barrel re-exports (`__init__.py`, `index.ts`), and namespace imports across all 7 full-tier languages.
- **Heritage extraction** — class inheritance and interface implementation for 11 languages (Python, TypeScript, JavaScript, Java, Go, Rust, C++, Kotlin, Ruby, C#, C) with `extends`/`implements` graph edges.
- **Leiden community detection** — two-level community detection (file communities from import edges, symbol communities from call/heritage edges) with cohesion scoring and heuristic labeling. Falls back to NetworkX Louvain when graspologic is unavailable.
- **Execution flow tracing** — 5-signal entry point scoring (fan-out ratio, in-degree, visibility, name pattern, framework hint) with BFS call-path discovery and cross-community classification.
- **Graph query indexes** (migration `0017`) — composite indexes for sub-millisecond graph queries.

#### Web UI
- **Graph Intelligence on Overview** — expandable community list (labels, cohesion, member counts) and execution flows panel with call-path traces on the overview dashboard.
- **Wiki sidebar** — new collapsible section showing PageRank and betweenness percentile bars, community label, and in/out degree for the current file.
- **Symbols drawer** — right panel with graph metrics, callers/callees (with confidence scores), and heritage (extends/implements) for class nodes.
- **Graph page** — community color mode uses real community labels from Leiden detection. Clicking a node opens a community detail panel. Active color mode preserved as a URL parameter.
- **Contributor network, hotspot, and ownership pages** — new dedicated pages for git intelligence.
- **Docs viewer** — enriched with graph intelligence sidebar, version history, and improved markdown rendering.
- **5 new graph REST API endpoints** — communities list, community detail, node metrics, callers/callees, and execution flows.

#### Other
- **Improved init UX** — pre-scan phase shows repo size and language breakdown before confirming. Advanced config options grouped logically with live insights during indexing.
- **Doc generation enriched with graph intelligence** — wiki page generation prompts now include community context, caller/callee information, and heritage relationships.

### Changed
- **`get_overview`** now includes `community_summary` — top communities by size with labels and cohesion scores.
- **`get_context`** now includes `community` block per file target with community ID and label (when `compact=False`). In workspace mode, enriched with cross-repo co-change and contract data.
- **`get_risk`** enriched with cross-repo signals in workspace mode — co-change partners from other repos and contract dependencies.
- **`search_codebase`** in workspace mode searches across all repos and merges results.
- **Job executor** — improved progress tracking, concurrent run detection (HTTP 409), and crash recovery for stale running jobs on server startup.

---

## [0.2.3] — 2026-04-11

### Added
- **`annotate_file` MCP tool** — attach human-authored notes to any wiki page. Notes survive LLM-driven re-generation and appear in `get_context` responses and the web UI. Pass an empty string to clear notes.
- **`repowise export --full`** — full JSON export now includes decision records, dead code findings, git hotspots, and per-page provenance metadata (confidence, freshness, model, provider).
- **Rust import resolution** — `use crate::`, `super::`, and `self::` imports now resolve to local files via crate root detection (`lib.rs`/`main.rs`). External crates mapped to `external:` nodes.
- **Go import resolution** — `go.mod` module path parsing enables accurate local vs external package classification. Local imports resolve by suffix matching against the module path.
- **C/C++ parser improvements** — added captures for `template_declaration`, `type_definition` (typedef struct/enum), `preproc_def` (#define), `preproc_function_def`, and forward declarations.
- **Go parser** — added `const_spec` and `var_spec` captures for package-level constants and variables.
- **Rust parser** — added `macro_definition` capture for `macro_rules!` macros.
- **Dynamic import detection** — dead code analysis now scans for `importlib.import_module()` and `__import__()` calls; files in the same package receive reduced confidence (capped at 0.4).
- **Framework decorator awareness** — Flask, FastAPI, and Django route/endpoint decorators added to `_FRAMEWORK_DECORATORS`. Decorated functions are never flagged as dead code.
- **`human_notes` column on wiki pages** — persists across re-indexing. Alembic migration `0014_page_human_notes`.
- **Decision staleness scoring during ingestion** — `compute_staleness()` now runs during `repowise init`, not just `repowise update`.

### Changed
- **CLAUDE.md template** — replaced imperative "MUST use" / "CRITICAL" language with advisory framing. Added `indexed_commit` display. Made `update_decision_records` optional ("SHOULD for architectural changes").
- **`get_context` freshness** — freshness data now included by default instead of requiring explicit `include=["freshness"]`.
- **`get_answer` docstring** — removed "do NOT verify by Read" instruction. High-confidence note changed to "verify cited file paths exist before acting on them".
- **Token budget caps** — `get_overview` caps knowledge_silos (30), module_pages (20), entry_points (15). `get_why` caps file_commits (10).
- **Dead code patterns** — expanded `_DEFAULT_DYNAMIC_PATTERNS` with `*Mixin`, `*Command`, `*_view`, `*_endpoint`, `*_route`, `*_callback`, `*_signal`, `*_task`.

### Docs
- **README** — tool count updated to 11, `annotate_file` added to MCP tools table, `--full` export flag documented, dynamic import detection noted, comparison table updated.
- **Supported languages** — tiered table with accurate "What works" descriptions per language.
- Updated USER_GUIDE.md, ARCHITECTURE.md, and deep-dives.md to reflect all changes.

---

## [0.2.2] — 2026-04-11

### Added
- **tsconfig/jsconfig path alias resolution** (#40) — new `TsconfigResolver` discovers all `tsconfig.json` / `jsconfig.json` files, resolves `extends` chains (with circular detection), and maps path aliases (e.g. `@/*` -> `src/*`) to real files during graph construction. Non-relative TS/JS imports that match a path alias now create proper internal edges instead of phantom `external:` nodes. Fixes broken dependency graph, PageRank, dead code false positives, and change propagation for any TS/JS project using path aliases (Next.js, Vite, Angular, Nuxt, CRA).
- **Traversal stats** (#57) — `FileTraverser` now tracks skip reasons (`.gitignore`, blocked extension, binary, oversized, generated, `--exclude`, `.repowiseIgnore`, unknown language) via a new `TraversalStats` dataclass. Stats are surfaced after traversal as a filtering summary showing how many files were included vs excluded and why.
- **Submodule handling** (#57) — git submodule directories (parsed from `.gitmodules`) are now excluded by default during traversal. Added `--include-submodules` flag to `repowise init` to opt in.
- **Language breakdown** (#57) — generation plan table now shows language distribution (e.g. "Languages: python 79%, typescript 14%"). Completion panel shows top languages with percentages instead of just a count.
- **Multi-line exclude input** — interactive advanced mode now prompts for exclude patterns one per line instead of comma-separated on a single line.
- 38 new unit tests covering tsconfig resolver, traversal stats, and submodule handling.

### Changed
- Traverse progress bar uses spinner mode instead of showing misleading pre-filter totals (e.g. "2132/83601").
- Traverse phase label changed from "Traversing files..." to "Scanning & filtering files...".

### Fixed
- Server tests now use real temp directories with `.git` folders for path validation (#69 compatibility).

### Docs
- Updated README CLI reference with `--index-only`, `-x`, and `--include-submodules` examples.
- Updated website docs (`cli-reference.md`, `configuration.md`, `getting-started.md`) with submodule handling, `.gitignore` documentation, and new output examples.
- Reorganized `docs/` directory: architecture docs into `docs/architecture/`, internals into `docs/internals/`.
- Removed stale one-time documents (PHASE_5_5_IMPLEMENTATION, GIT_INTELLIGENCE_AUDIT, MCP_AND_STATE_REVIEW, MCP_TOOLS_TEST_REPORT).

---

## [0.2.1] — 2026-04-10

### Added
- **`get_answer` MCP tool** (`tool_answer.py`) — single-call RAG over the wiki layer. Runs retrieval, gates synthesis on top-hit dominance ratio, and returns a 2–5 sentence answer with concrete file/symbol citations plus a `confidence` label. High-confidence responses can be cited directly without verification reads. Backed by an `AnswerCache` table so repeated questions on the same repository cost nothing on the second call.
- **`get_symbol` MCP tool** (`tool_symbol.py`) — resolves a fully-qualified symbol id (`path::Class::method`, also accepts `Class.method`) to its source body, signature, file location, line range, and docstring. Returns the rich source-line signature (with base classes, decorators, and full type annotations preserved) instead of the stripped DB form.
- **`Page.summary` column** — short LLM-extracted summary (1–3 sentences) attached to every wiki page during generation. Used by `get_context` to keep context payloads bounded on dense files. Added by alembic migration `0012_page_summary`.
- **`AnswerCache` table** — memoised `get_answer` responses keyed by `(repository_id, question_hash)` plus the provider/model used. Added by alembic migration `0013_answer_cache`. Cache entries are repository-scoped and invalidated by re-indexing.
- **Test files in the wiki** — `page_generator._is_significant_file()` now treats any file tagged `is_test=True` (with at least one extracted symbol) as significant, regardless of PageRank. Test files have near-zero centrality because nothing imports them back, but they answer "what test exercises X" / "where is Y verified" questions; the doc layer is the right place to surface those. Filtering remains available via `--skip-tests`.
- **Overview dashboard** (`/repos/[id]/overview`) — new landing page for each repository with:
  - Health score ring (composite of doc coverage, freshness, dead code, hotspot density, silo risk)
  - Attention panel highlighting items needing action (stale docs, high-risk hotspots, dead code)
  - Language donut chart, ownership treemap, hotspots mini-list
  - Decisions timeline, module minimap (interactive graph summary)
  - Quick actions panel (sync, full re-index, generate CLAUDE.md, export)
  - Active job banner with live progress polling
- **Background pipeline execution** — `POST /api/repos/{id}/sync` and `POST /api/repos/{id}/full-resync` now launch the full pipeline in the background instead of only creating a pending job. Concurrent runs on the same repo return HTTP 409.
- **Shared persistence layer** (`core/pipeline/persist.py`) — `persist_pipeline_result()` extracted from CLI, reused by both CLI and server job executor
- **Job executor** (`server/job_executor.py`) — background task that runs `run_pipeline()`, writes progress to the `GenerationJob` table, and persists all results
- **Server crash recovery** — stale `running` jobs are reset to `failed` on server startup
- **Async pipeline improvements** — `asyncio.wrap_future` for file I/O, `asyncio.to_thread` for graph building and thread pool shutdown, periodic `asyncio.sleep(0)` yields during parsing
- **Health score utility** (`web/src/lib/utils/health-score.ts`) — composite health score computation, attention item builder, and language aggregation for the overview dashboard

### Changed
- **`get_context` default is now `compact=True`** — drops the `structure` block, the `imported_by` list, and per-symbol docstring/end-line fields to keep the response under ~10K characters. Pass `compact=False` for the full payload (e.g. when you specifically need import-graph dependents on a large file).
- `init_cmd.py` refactored to use shared `persist_pipeline_result()` instead of inline persistence logic
- Pipeline orchestrator uses async-friendly patterns to keep the event loop responsive during ingestion
- Sidebar and mobile nav updated to include "Overview" link

- Monorepo scaffold: uv workspace with `packages/core`, `packages/cli`, `packages/server`, `packages/web`
- Provider abstraction layer: `BaseProvider`, `GeneratedResponse`, `ProviderError`, `RateLimitError`
- `AnthropicProvider` with prompt caching support
- `OpenAIProvider` with OpenAI Chat Completions API
- `OllamaProvider` for local offline inference (OpenAI-compatible endpoint)
- `LiteLLMProvider` for 100+ models via LiteLLM proxy
- `MockProvider` for testing without API keys
- `RateLimiter`: async sliding-window RPM + TPM limits with exponential backoff
- `ProviderRegistry`: dynamic provider loading with custom provider registration
- CI pipeline: GitHub Actions matrix on Python 3.11, 3.12, 3.13
- Pre-commit hooks: ruff lint + format, mypy, standard file checks
- **Folder exclusion** — three-layer system for skipping paths during ingestion:
  - `FileTraverser(extra_exclude_patterns=[...])` — pass gitignore-style patterns at construction time; applied to both directory pruning and file-level filtering
  - Per-directory `.repowiseIgnore` — traverser loads one from each visited directory (like git's per-directory `.gitignore`); patterns are relative to that directory and cached for efficiency
  - `repowise init --exclude/-x PATTERN` — repeatable CLI flag; patterns are merged with `exclude_patterns` from `config.yaml` and persisted back to `.repowise/config.yaml`
  - `repowise update` reads `exclude_patterns` from `config.yaml` automatically
  - Web UI **Excluded Paths** section on `/repos/[id]/settings`: chip editor, Enter-to-add input, six quick-add suggestions (`vendor/`, `dist/`, `build/`, `node_modules/`, `*.generated.*`, `**/fixtures/**`), empty-state message, gitignore-syntax tooltip; saved via `PATCH /api/repos/{id}` as `settings.exclude_patterns`
  - `helpers.save_config()` now round-trips `config.yaml` to preserve all existing keys when updating provider/model/embedder; accepts optional `exclude_patterns` keyword argument
  - `scheduler.py` logs `repo.settings.exclude_patterns` in polling fallback as preparation for future full-sync wiring
- 13 new unit tests in `tests/unit/ingestion/test_traverser.py` covering `extra_exclude_patterns` and per-directory `.repowiseIgnore` behaviour

---

## [0.2.0] — 2026-04-07

A large overhaul: faster indexing, smarter doc generation, transactional storage,
new analysis capabilities, and a completely revamped web UI that surfaces every
new signal — all without changing the eight MCP tool surface.

### Added

#### Pipeline & ingestion
- **Parallel indexing.** AST parsing now runs across all CPU cores via
  `ProcessPoolExecutor`. Graph construction and git history indexing run
  concurrently with `asyncio.gather`. Per-file git history fetched through a
  thread executor with a semaphore.
- **RAG-aware doc generation.** Pages are generated in topological order; each
  generation prompt now includes summaries of the file's direct dependencies,
  pulled from the vector store of already-generated pages.
- **Atomic three-store coordinator.** New `AtomicStorageCoordinator` buffers
  writes across SQL, the in-memory dependency graph, and the vector store, then
  flushes them as a single transaction. Failure in any store rolls back all three.
- **Dynamic import hint extractors.** The dependency graph now captures edges
  that pure AST parsing misses: Django `INSTALLED_APPS` / `ROOT_URLCONF` /
  `MIDDLEWARE`, pytest `conftest.py` fixture wiring, and Node/TS path aliases
  from `tsconfig.json` and `package.json` `exports`.

#### Analysis
- **Temporal hotspot decay.** New `temporal_hotspot_score` column on
  `git_metadata`, computed as `Σ exp(-ln2 · age_days / 180) · min(lines/100, 3)`
  per commit. Hotspot ranking now uses this score; commits from a year ago
  contribute ~25% as much as commits from today.
- **Percentile ranks via SQL window function.** `recompute_git_percentiles()`
  is now a single `PERCENT_RANK() OVER (PARTITION BY repo ORDER BY ...)` UPDATE
  instead of an in-Python sort. Faster and correct on large repos.
- **PR blast radius analyzer.** New `PRBlastRadiusAnalyzer` returns direct
  risks, transitive affected files, co-change warnings, recommended reviewers,
  test gaps, and an overall 0-10 risk score. Surfaced via `get_risk(changed_files=...)`
  and a new web page.
- **Security pattern scanner.** Indexing now runs `SecurityScanner` over each
  file. Findings (eval/exec, weak crypto, raw SQL string construction,
  hardcoded secrets, `pickle.loads`, etc.) are stored in a new
  `security_findings` table.
- **Knowledge map.** Top owners, "bus factor 1" knowledge silos (>80% single
  owner), and high-centrality "onboarding targets" with thin documentation --
  surfaced in `get_overview` and the web overview page.

#### LLM cost tracking
- New `llm_costs` table records every LLM call (model, tokens, USD cost).
- `CostTracker` aggregates session totals; pricing covers Claude 4.6 family,
  GPT-4.1 family, and Gemini.
- New `repowise costs` CLI: `--since`, `--by operation|model|day`.
- Indexing progress bar shows a live `Cost: $X.XXXX` counter.

#### MCP tool enhancements (still 8 tools -- strictly more capable)
- `get_risk(targets, changed_files=None)` -- when `changed_files` is provided,
  returns the full PR blast-radius report (transitive affected, co-change
  warnings, recommended reviewers, test gaps, overall 0-10 score). Per-file
  responses now include `test_gap: bool` and `security_signals: list`.
- `get_overview()` -- now includes a `knowledge_map` block (top owners, silos,
  onboarding targets).
- `get_dead_code(min_confidence?, include_internals?, include_zombie_packages?)` --
  sensitivity controls for false positives in framework-heavy code.

#### REST endpoints (new)
- `GET /api/repos/{id}/costs` and `/costs/summary` -- grouped LLM spend.
- `GET /api/repos/{id}/security` -- security findings, filterable by file/severity.
- `POST /api/repos/{id}/blast-radius` -- PR impact analysis.
- `GET /api/repos/{id}/knowledge-map` -- owners / silos / onboarding targets.
- `GET /api/repos/{id}/health/coordinator` -- three-store drift status.
- `GET /api/repos/{id}/hotspots` now returns `temporal_hotspot_score` and is
  ordered by it.
- `GET /api/repos/{id}/git-metadata` now returns `test_gap`.
- Job SSE stream now emits `actual_cost_usd` (running cost since job start).

#### Web UI (new pages and components)
- **Costs page** -- daily bar chart, grouped tables by operation/model/day.
- **Blast Radius page** -- paste files (or click hotspot suggestion chips) to
  see risk gauge, transitive impact, co-change warnings, reviewers, test gaps.
- **Knowledge Map card** on the overview dashboard.
- **Trend column** on the hotspots table with flame indicator (default sort).
- **Security Panel** in the wiki page right sidebar.
- **"No tests" badge** on wiki pages with no detected test file.
- **System Health card** on the settings page (SQL / Vector / Graph counts +
  drift % + status).
- **Live cost indicator** on the generation progress bar.

#### CLI
- `repowise costs [--since DATE] [--by operation|model|day]` -- new command.
- `repowise dead-code` -- new flags `--min-confidence`, `--include-internals`,
  `--include-zombie-packages`, `--no-unreachable`, `--no-unused-exports`.
- `repowise doctor` -- new Check #10 reports coordinator drift across all
  three stores. `--repair` deletes orphaned vectors and rebuilds missing graph
  nodes from SQL.

### Fixed
- C++ dependency resolution edge cases.
- Decision extraction timeout on very large histories.
- Resume / progress bar visibility for oversized files.
- Coordinator `health_check` falsely reporting 100% drift on LanceDB / Pg
  vector stores (was returning -1 for the count). Now uses `list_page_ids()`.
- Coordinator `health_check` returning `null` graph node count when no
  in-memory `GraphBuilder` is supplied. Now falls back to SQL `COUNT(*)`.

### Internal
- Three new Alembic migrations: `0009_llm_costs`, `0010_temporal_hotspot_score`,
  `0011_security_findings`.

### Compatibility
- Existing repositories must run migrations: `repowise doctor` will detect
  the missing tables and prompt; alternatively re-run `repowise init` to
  rebuild from scratch.
- The eight MCP tool names and signatures are backwards compatible -- new
  parameters are all optional.

---

## [0.1.31] — earlier

See git history for releases prior to 0.2.0.

---

[0.3.1]: https://github.com/repowise-dev/repowise/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/repowise-dev/repowise/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/repowise-dev/repowise/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/repowise-dev/repowise/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/repowise-dev/repowise/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/repowise-dev/repowise/compare/v0.1.31...v0.2.0
