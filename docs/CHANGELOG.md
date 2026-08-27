# Changelog

All notable changes to repowise will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Use `git-cliff` to auto-generate entries from conventional commits -->

---

## [0.46.0] — 2026-08-27

The headline this cycle is cross-repo contracts becoming a surface you can use rather than a graph the indexer knew about. A workspace now recognises routes once and serves them to both producers and consumers, binds each contract to a real symbol id, reads request schemas off handler signatures, and gives the whole thing a front door in the UI and over REST. Alongside it the MCP tool surface got a shared response ceiling with recoverable pagination, so no tool can silently truncate an answer with no way back, and the C++, Rust, Java and TypeScript call graphs each lost a class of wrong edge.

### Added

- **Cross-repo contracts have a front door.** A workspace's contracts are browsable, open from the table into what they link to, and carry the line, symbol and schema over REST (#1890, #1899). One route recogniser now serves Go, Laravel and Axum, with Django, JAX-RS, Next.js and the Hono router DSL alongside them (#1867, #1860), and a library's public API counts as a contract in its own right.
- **Python HTTP consumers resolve through their client bindings**, so a call made through a session or a generated client still names the path and verb it actually reaches.
- **A shared response ceiling across the MCP tools.** The five uncapped tools now answer within a canonical budget enforced at delivery, and every cap is recoverable: relationship, history and truncated test lists all carry a way back to the rows they dropped (#1924, #1930).
- **Canonical references that round-trip.** A reference emitted by one tool can be passed to another, and `get_why` returns evidence references pointing at what it read (#1927, #1932).
- **`claude_cli` provider**, for indexing against a Claude Code subscription instead of an API key (#1514).
- **The knowledge graph says what it is not showing** (#1847), its arrows mean execution rather than mere reference (#1832), and a folder card is named after the module page documenting it (#1828).

### Changed

- **BREAKING (MCP): `get_risk`'s `will_break` field is now `may_break`** (#1892). The old name asserted a certainty the structural heuristic behind it does not have. Any client reading `will_break` must be updated.
- **`get_overview`'s onboarding blocks are opt-in**, and `get_answer` and `get_overview` return leaner payloads for the same content.
- **Retrieval precision improved** for `get_answer`, which can now see the call graph when expanding a question (#1933).
- **`HEALTH_ANALYZER_VERSION` is 6 and `PARSER_SCHEMA_VERSION` is 2.** Both are picked up automatically: an existing index re-scores health on its next update rather than waiting out the decay timer, and the parse cache re-parses once so the call-graph fixes below actually apply. Neither forces a re-index.

### Fixed

- **C++ call resolution**: scoped `Ns::f()` calls resolve against their qualifier rather than falling to a bare name (#1915), chained calls are constrained by the inner callee's return type (#1782), in-class method declarations and receivers typed from declarations are extracted, types used only inside their own translation unit are rescued, types behind export macros parse, and an overload set resolves instead of being refused.
- **A Rust macro invocation is no longer counted as a function call** (#1885), a Java chain rooted at an external type is no longer read as a bare-name call, and the repo-wide receiver tier no longer answers for foreign types.
- **Health scoring inputs**: Python `match` arms count toward cyclomatic complexity (#1795), Pascal else-if chains flatten instead of nesting each arm (#1679), a comment inside a parameter list is no longer counted as a parameter (#1796), and two test-detection gaps closed, one a pairing the name heuristic could not see (#1707) and one Delphi's `Test<Stem>.dpr` convention (#1706).
- **`is_hotspot` is emitted by the backend** rather than re-derived in the client (#1797), fix density ranks against commits rather than individual files, and `tests_to_run` ranks by files reached rather than alphabetically.
- **`coverage add` exits non-zero when it stores nothing** (#1751), and config errors that cannot self-heal no longer promise a retry.
- **The file page renders again**: a pure `?tab=` helper had been exported from a client module and called from the server (#1936).
- **API timestamps render as UTC** (#1835), `CLAUDE.md` is stamped with the indexed commit rather than live `HEAD`, and a `SELECT INTO` target is no longer flagged as a cartesian join.

### Documentation

- The benchmarks pages lead each section with its result, pair precision with recall so neither reads alone, and state both cost denominators (#1928).
- The MCP tool table's drifted rows and the README's tool-count claims are corrected and asserted by a test.

---

## [0.45.0] — 2026-08-21

The headline this cycle is test intelligence that needs no coverage report. 0.44.0 made the call graph good enough to lead with; this release walks it to answer which tests reach a file, serves that through the API, and turns the Coverage tab into a Tests tab that says something useful on a repository that has never ingested a report. Around it, Code Health's performance findings become bounded opportunities that open the matching refactoring plan, Eden AI joins as an EU-hosted provider and embedder, and pinning BLAS threads before numpy loads halves peak memory on a cold index.

### Added

- **A test-to-code map that needs no coverage report.** The per-test map only
  ever existed if you ingested a coverage report with contexts, so on most
  repositories `tests_to_run` was empty, `impacted_tests` said "run the full
  suite", and `untested_hotspot` fell back to matching filenames. The call graph
  already records which test can execute into which source file, and that
  relation now answers when the measured one cannot. It is labelled `inferred`
  everywhere, never blended with measured coverage, and never turned into a
  percentage. Nothing is stored: it is a bounded walk over rows already indexed,
  measured at 63 ms once per health run on a 3,700-file repository.

  The call graph is the primary signal and the import graph the weaker fallback,
  which is a measured choice rather than a preference. Dogfooded against a real
  `coverage run --contexts=test` over a slice where per-test attribution is
  complete, with both sides seeing the same 37 test files: walking imports one
  hop scored 72.1% precision at 19.5% recall, walking calls three hops scored
  91.7% at 27.7%, and dropping the one call-resolution strategy that only
  matches a name repo-wide (`global_unique`) took that to 95.7% at no cost to
  recall. Unioning the two is worse than either lead, so the import tier is
  spent only on the files the call graph says nothing about, where it answers
  one more of them at no loss of precision.

  New fields: `tests_to_run_basis` on `get_risk`'s directive
  (`measured` / `inferred` / `none`), `basis` and a `status: "inferred"` on
  `get_change_risk`'s `impacted_tests`, and a `via` marker on every
  `repowise impacted-tests` candidate saying which tier answered.

- **Code Health performance opportunities now lead into the existing
  Refactoring workflow.** A dedicated Performance tab groups raw findings into
  bounded causal opportunities with production/tooling versus test context,
  affected-call-site totals, confidence, provenance paths, and raw-evidence
  paging. Exact stable opportunity ids open matching `performance_fix` plans;
  opportunities without a safe plan say so instead of selecting a nearby one.
  The Refactoring page adds a Performance filter and shared priority,
  validation, path, and pagination renderers. Its new server-owned page endpoint
  bounds initial payloads while preserving the legacy unpaged route. VS Code
  consumes the same contracts and renders priority and validation without web
  dependencies; older missing optional fields remain supported.

- **`/health/tests-reaching?file_path=`** answers which tests reach one file,
  with `via` separating a test whose calls run into it from one that only imports
  it, and `/health/coverage` falls back to the inferred map under
  `basis: "inferred"` when no report was ingested. The two bases never share a
  field, so a consumer cannot render derived data through the measured code path
  by accident. `include_inferred=false` declines the fallback for a caller that
  only wants the cheap badge number. (#1757)

- **Eden AI is a first-class LLM provider and embedder.** An EU-headquartered
  gateway exposing 700+ models behind an OpenAI-compatible endpoint, so it reuses
  the existing `openai` dependency with a custom base URL and adds no new one.
  `EDENAI_API_KEY`, `EDENAI_BASE_URL` for the EU endpoint, and an embedder
  defaulting to a 1024-dimension EU model. It is appended to the autodetect order
  rather than inserted, so an unrelated key in the environment cannot move anyone
  off the provider they were already resolving to. (#705)

### Changed

- **The Coverage tab is now a Tests tab, and it answers without a report.** The
  URL stays `/coverage` and "coverage" keeps meaning the measured thing inside
  the tab; what changed is that the empty state no longer tells you to go
  ingest a report while claiming nothing is inferred. On the inferred basis the
  chart collapses to two positions rather than a 0-100 continuum, which is the
  honest statement about the evidence, and nothing is rendered as a percentage
  or a bar. The file page names the tests that reach it, with `via` separating a
  test whose calls run into the file from one that only imports it. (#1758)

- **`untested_hotspot` stops accusing files that the tests run.** With no
  coverage ingested it fired on any hotspot without a *paired test file*, which
  is a filename convention, so a suite that names its tests for behaviour
  satisfied nothing. A test whose calls reach the file now suppresses it too. On
  this repository five of the six worst bug-magnet files had no test named for
  them and read as untested; the sixth, `analysis/health/engine.py`, was called
  tested because the convention matched `distill/test_engine.py` on basename
  alone. The same floor now runs under `get_risk`'s `missing_tests`, which had
  two separate filename heuristics that disagreed.

  Measured on repowise's own index, against the 80 standing `untested_hotspot`
  findings the filename convention leaves behind: the call graph clears 32 of
  them, 22 graded high and 3 critical, where a one-hop import walk clears 11.
  The persisted `has_test_file` widens to match, so the file table stops
  labelling those same files "untested" while the biomarker says nothing.

  Both stored values are wrong on an index built before this, so
  `HEALTH_ANALYZER_VERSION` moves to 3 and the next `repowise update` with
  changed files re-scores health rather than waiting out the decay timer.

- **`repowise impacted-tests --format json` renames `guessed_tests` to
  `inferred_tests`.** The bucket no longer holds only filename guesses, so the
  old name described the wrong thing; each entry carries `via` (`call-graph`,
  `import-graph` or `filename-pattern`) to say which tier answered.

- **Refactoring recommendations have one contract.** REST, MCP and the CLI each
  rebuilt their own ranking and payload shape, and the TypeScript contract was
  duplicated in the UI package. The wire contract moves to
  `@repowise-dev/types` and the Python side gets a single read model owning
  rehydration, enrichment, ranking, validation and serialization for every
  surface. Blast radius now raises cost and risk rather than benefit, so a wide
  change ranks as expensive instead of valuable. (#1784)

- **The semantic leg of search gets a budget the cold path can clear.** The first
  vector query in a process pays for the store open, the first embed and the
  first ANN probe. That was measured at 6.3s and 13.4s on a cold Windows index
  where a warm query takes 0.19s, against a hardcoded 8s bound inside a
  suppressed exception. The budget is now one shared 30s default, capped at 120s and
  overridable with `REPOWISE_VECTOR_SEARCH_TIMEOUT_S`, and a timeout logs a
  warning naming the knob instead of silently degrading to full-text with
  nothing said. (#1678, #1685)

- **Plugin**: `/repowise:impacted-tests` and the `change-review` skill describe
  the inferred map as the call graph with an import-graph fallback, and `init` /
  `reindex` list the embedders the CLI actually accepts.

- **Execution-graph analysis is unified** behind one code path in health, with
  the legacy call fallback restricted rather than left answering alongside it.
  (#1773)

### Fixed

- **An incremental update stopped erasing ingested coverage.** The incremental
  pipeline built its analyzer without a coverage map, so every changed file was
  re-scored as if no report had ever been ingested and the partial-health writer
  upserted `line_coverage_pct` back to NULL, eroding coverage one file per
  update, starting with the files under active development. Both callers now load
  and supply the persisted map. (#1739, #1806)
- **`repowise init` no longer crashes in a git worktree.** `.git` is a file
  there, not a directory, so building `.git/hooks` by hand raised
  `NotADirectoryError` and took the whole command down. The hooks directory is
  resolved with `git rev-parse --git-path hooks`, which answers correctly for a
  worktree and a normal clone alike. (#1609, #1808)
- **The CLI and the MCP server resolve the same embedder key.** The server never
  consulted the process environment, so on a machine with an exported provider
  key different from the repository's `.repowise/.env`, semantic search degraded
  through the CLI while the MCP tool answered. The server now reads the env tier
  first, matching the CLI's "an exported key is an explicit override" contract.
  (#1711, #1810)
- **`edenai` is selectable everywhere it is documented**: `init --embedder`,
  `reindex --embedder`, both interactive prompts, `EDENAI_API_KEY` autodetection
  and the server's embedder build, four lists that were left on the older
  five-backend set. (#1815, #1820)
- **`repowise reindex` exits non-zero when every embed failed**, so an automated
  pipeline cannot treat an empty vector index as a successful build. An empty
  wiki still exits 0. (#1495, #1732)
- **The file page states how many tests reach a file, not how many it listed.**
  The list is capped at 50 and the page rendered that cap as the measurement; it
  now leads with the true count and says what it is showing. (#1764)
- **A generated-file banner has to be a banner.** The marker is required within
  the first two lines instead of substring-matched across the whole header, which
  fixes the class of prose false positives across all six markers. (#1519, #1731)
- **Python resolution**: `from pkg import submodule` pinned the binding at the
  package `__init__.py`, so a later `submodule.symbol()` call resolved against a
  file declaring nothing and dead code called the symbol safe to delete (#1193,
  #1733); aliased submodule bindings are pinned properly (#1255, #1785).
- **Go**: a call through a field (`a.b.Method()`) matched no pattern and was
  missing from the graph entirely rather than present and unresolved (#1727), and
  an embed of `io.Reader` keeps its package qualifier instead of binding to
  whatever repo-local `Reader` shares the short name, which made the type
  inherit from itself when the enclosing type had that name (#1726).
- **TypeScript**: `#private` class members produce a symbol and a call site
  (#1715, #1721), a return type no longer renders with a stray colon
  (`-> : string`) (#1695, #1719), and a relative import probes `index.tsx` and
  `index.jsx` (#1725).
- **Java and C# method return types are preserved** in signatures (#1713).
- **The bare-name tier stops answering for the standard library** (#1708), and
  barrel-file definitions are aligned with `file_reachability` (#1491, #1557).
- **The paired-test filename heuristic is shared** rather than reimplemented per
  surface (#1750).
- **Security scanning**: `replace_findings` no longer loses rows on a duplicate
  key (#1523), and a multi-line `subprocess.*(... shell=True)` call is flagged
  (#1522).
- **Agent CLIs no longer load the target repository's instruction files** when
  invoked for generation (#1100).
- **`workspace init --dry-run` writes nothing** (#1526), and `init --test-run`
  actually limits generation to the top 10 files (#1525).
- **`share_of_repo_gap_pct` uses the gross gap as its denominator** (#1452), and
  history fetches avoid the partial-clone path (#1608).
- **The `get_risk` artifact renders in chat.** The renderer mapped over a list
  and read `path` / `churn_percentile`, but the tool returns a path-keyed dict of
  `hotspot_score` / `file_path`, so opening the artifact threw or showed blank
  rows. Both shapes are normalized, and the 0-1 score is scaled for display
  rather than printed as though it were already a percentage. (#1480)

### Performance

- **Peak memory on a cold index is roughly halved.** Importing numpy brings up a
  BLAS runtime that commits a private per-thread workspace sized to the host's
  core count: 746 MB on a 32-core machine for the import alone, committed once,
  never returned, and invisible to `tracemalloc` because no Python object owns
  it. The pipeline's only numpy work is PageRank over a sparse graph, which runs
  single-threaded in well under a second, so the thread count is pinned before
  numpy is imported. A cold `init --no-prose` on an 876-file repository went from
  1,547 MB to 818 MB at no wall-clock cost. `OMP_NUM_THREADS` is deliberately
  left alone: igraph community detection really is OpenMP-parallel, and pinning
  it cost 18% wall clock for no further saving. (#1394, #1776)

### Documentation

- **A roadmap, an enterprise section, and a benchmarks page that leads with its
  results.** `ROADMAP.md` is new and states plainly that language support is
  never gated behind the licence; the README gains a real enterprise section
  covering deployment, data boundary, compliance and contract; `BENCHMARKS.md`
  goes from 777 visible lines to 456 with every caveat and every losing row
  preserved behind folds. Language support is now stated the same way in every
  document: 19 parsed to a full AST, 35 on the ladder. Source control beyond git
  (Perforce, Subversion, and the mainframe promotion hierarchies) is scoped to
  the one history-derived layer that actually needs it. (#1766)
- **The graph precision result is published**, judged by a compiler across five
  tools (#1738), and `COMMERCIAL.md`'s tool count matches the way the document
  counts (#1771).

---

## [0.44.0] — 2026-08-18

The theme this cycle is the call graph knowing who the receiver is. A call written `user.save()` only becomes an edge if something can say what `user` is, and for most languages nothing could: the resolver matched the bare name against every symbol in the repo and guessed. Receiver typing now runs for Go, Kotlin, Swift, C#, Java, Python, PHP and Luau, and resolution follows re-export chains. On microdot, a small pure-Python repo with none of the languages that gained most, call edges went from 876 to 1,371. Dead code got the matching precision pass, and updates now carry both onto an index built by an older version, which they previously did not. Expect the first `repowise update` after upgrading to run long, once.

### Added

- **Receiver typing across eight languages.** A call's receiver is typed from its declaration in Go (#1674), Kotlin (#1687), Swift (#1688) and C# (#1680), from the enclosing class for a bare call plus PHP and Luau receivers (#1630), from a field's declaring class (#1642), from a local's declaration (#1639), from assignments in a Python function body (#1643), from a Java call on its own field (#1658), and from a framework decorator that retyped it (#1684). Three more receiver shapes a bare-name match had been guessing at are now captured directly (#1686).
- **Resolution follows re-exports.** A call resolves through the name a module publishes (#1682), a method on a type imported through a re-export (#1664), and a namespace member through the whole re-export chain (#1672).
- **`dispatches_to` edges** link a base method to the implementations that answer for it (#1649), and a framework-wired symbol to the symbol it is wired to (#1654).
- **Every call edge records the strategy that resolved it** (#1628), and the web UI says how each edge got into the graph (#1652). An execution flow now says why it stopped instead of just stopping (#1650).
- **Security findings carry a verified line and a commit date** (#1668).
- **Pascal is registered in the complexity, duplication and dataflow dialects**, so health scores it like the other 18 languages (#1629).

### Changed

- **The file detail page is ported onto the design language** (#1621) and gained inbound and outbound navigation (#1622).
- **The workspace System Map moves its chrome off the canvas**, and the per-repo view works (#1616).
- **Dead code surfaces staleness on the web** and drops the package column (#1669).
- **`this.method()` self-dispatch is recorded in six languages** (#1617).
- **Export aliases are read from the parser** rather than a second scan of the file (#1683).

### Fixed

- **An extraction change now reaches unchanged files.** An incremental update rewrote only the git-changed files' rows in `graph_edges`, which is right for a content change and wrong for a parser change: the latter alters every file's edges at once. The build that wrote a repo's edges is now recorded, and a mismatch widens the reconcile to the whole parsed set once before re-stamping. Without this, none of this release's graph work would reach an existing index short of a full re-index. (#1619)
- **Dead-code analysis no longer skips itself on update.** Stored commit timestamps came back without a timezone while freshly read ones carried one, so ageing a package raised `TypeError`, a broad catch turned it into a one-line warning, and every finding silently kept its previous verdict. Present since 0.40.0. (#1702)
- **Symbols reached by a framework or a container** (#1673), **wired in by a registration decorator** (#1681), or **named by a docs build or an API dump** (#1677) are no longer reported as dead.
- **Every use of a private symbol counts**, not only a call (#1662). Only genuinely narrow scopes stay in the uncalled-symbol pool (#1646), and a top-tier confidence means the checks behind it ran (#1666).
- **C and C++**: a forward-declared type is not an unused export (#1700), nor is a template forward declaration (#1703); a call edge attaches to the definition rather than the header declaration (#1626); a function named but never called counts as a use (#1627); `.inl` / `.ipp` / `.tpp` (#1625) and `.hh` are recognised as C++, and `.inc` is reassigned from Pascal (#1693).
- **Every language gives one answer for a type's bare name** (#1634). A struct field is not callable, so the bare-name tier no longer offers one (#1692), and Rust type positions are filed as `type_use` rather than calls (#1690).
- **Inheritance is emitted for JavaScript classes** (#1636) and Go interface embedding (#1641). C# resolves inherited calls again (#1651), captures generic method calls (#1637), and records the visibility a declaration actually has (#1644).
- **A third-party JVM import no longer takes a same-named repo class** (#1659). A Go method whose receiver is unexported is typed (#1676). A workspace package that publishes only from a build directory binds correctly (#1670).
- **A method passed as a value is separated from a method called** (#1661).
- **MCP**: four tools answered a bad argument with a reassuring negative instead of an error (#1671); `get_context` counted a subclass and a fixture as callers (#1663); raw doubles and two different scales shipped under one key (#1631). The symbol page made the same subclass mistake (#1660).
- **A failed framework pass is reported** rather than swallowed (#1645).
- **`repowise update --full` runs under the single-flight lock** (#1529), and the incremental hotspot gate reuses the persisted function-modification p80 (#1532).
- **ADR discovery no longer mines paths git cannot see** (#1614), the security history scanner actually scans (#1667), and fix history ignores the shallow-clone boundary (#1633).

### Performance

- **Graph build stops re-reading the repository** (#1648), and each language gets a call-strategy seam that rejects unresolvable names first (#1632).
- **The file page stops reading the whole repo to render one file** (#1618).
- **Workspace `.csproj` scanning walks each repo once** (#1615).

### Documentation

- The language-support pages are rewritten, a graph layer page is added, and the claims that pointed at them are corrected (#1689).

---

## [0.43.0] — 2026-08-15

The through-line this cycle is a number meaning what it says. Change risk scored the size of the diff and presented it as danger. `get_answer` could return `confidence: high` beside `retrieval_quality: weak`, with a note that asserted dominance and denied it in the same sentence. `get_why` answered every question, including the ones its store knew nothing about. The dashboard summed dead exports across a side project and a monorepo. Six surfaces each computed hotspot health their own way and none of them agreed. Each of those is fixed by giving the answer one owner and reporting the reason alongside it. `repowise risk` now leads with the bug-fix history of the files a change touches, an answer's confidence note quotes the test that earned it, and a question the decision store cannot answer gets a redirect instead of the three closest records.

The second theme is what a keyless install gets. `get_answer` with embeddings but no LLM key used to return a ranked file list and nothing else, though the evidence around it (live symbol bodies, candidate justifications, rationale comments) needs no provider at all. It builds all of that now, and rates its retrieval instead of only doubting itself. `init` also stopped losing an API key supplied through the environment, which is the path agents and CI take.

Underneath, the graph got a good deal more honest: the edge-type vocabulary is now true and enforced, dependency answers stopped including containment and co-change rows, and "can execution start here" has one owner instead of four.

### Added

- **Object Pascal / Delphi support** (Good tier), taking the language count to 19. Symbols, imports, heritage, and a call graph that handles the parenless calls Pascal is written with. Verified against a 151-file real Delphi codebase. (#1353)
- **Dead code detects deprecation annotations.** `@Deprecated`, `[Obsolete]`, `@deprecated` and their language siblings now feed a finding's confidence. (#1472)
- **`repowise decision add` records from flags.** A flag per field, and no prompting at all once `--title` and `--decision` are both present, so a script or an agent with no terminal can record one. `--format json` returns the id to pass back to `confirm` or `show`. Flag-driven records land `proposed` where the prompts record `active`. (#1566)
- **Record a decision from the web UI.** The decisions page had no way to start a record and an empty state that sent you to a terminal. It now has a form, reachable even on a repo with no decisions yet. (#1567)
- **`GET /api/repos/summary`** returns every repository's headline figures in one call, replacing the `2N+1` requests the multi-repo dashboard used to make. Flat in the number of repos: six queries at 1 repo, six at 26. (#1577)
- **`repowise workspace scan --exclude`**, plus a bulk decline, so a scan of a large tree does not have to be answered repo by repo. (#1591)

### Changed

- **`repowise risk` and `get_change_risk` lead with fix history, not the score.** The 0-10 score restates the size of the diff: scoring by lines added alone reproduces it to within 0.16 points, and on 47 within-repo pairs across four repos it ranked the dangerous change above the boring one in none of them. The response now leads with `fix_history`, the recency-weighted bug-fix record of the files the change touches, which files carry it, and where that sits among the repo's own fix-bearing files. Ranking by that alone gets 46 of the same 47 pairs. It comes from one `git log` walk, so it needs no index and no coverage data. The score is kept and reported as what it measures, diff size and spread, named in `score_measures`. Model constants are unchanged. (#1593)
- **Change-risk numbers say what they are.** `probability` is gone (it was `score / 10` with extra decimals). The absolute band is now `fallback_band` and appears only when there was no baseline to rank against. A new `score_unit` states that the scale is calibrated on individual commits, so a squash-merged PR or a `base..head` range reads high by construction. `nf`/`nd`/`ns` are no longer reported as drivers: their coefficients are collinearity with diff size, so as an explanation they contradicted themselves. (#1583)
- **Default OpenAI model is now `gpt-5.6-luna`** (was `gpt-5.4-nano`). Same input price ($0.20/MTok), cheaper output ($1.20 vs $1.25), so it gets its own pricing rows rather than an alias, and nano stays selectable on its own rate. The 5.6 family also changed its reasoning ladder: `minimal` is gone and `xhigh` is new, so `--reasoning minimal` is rejected up front for these models instead of failing as a 400 on the first live call. Despite what the model docs say, the API also rejects `max` for 5.6 (verified live against both `gpt-5.6-luna` and `gpt-5.6-sol`), so it is not offered. (#1594)
- **`get_answer` serves real evidence with no LLM key.** An install with embeddings and no provider got a ranked file list and nothing else, though none of what it lost needs a provider: bodies are read live off disk at the indexed anchors. It now builds `symbol_bodies`, `best_guesses` and `code_rationale`, cites the paths they came from, and names one next action. It also rates the retrieval rather than only reporting `confidence: low`. On 26 paired questions, `symbol_bodies` went from 2/26 to 11/26 keyless. (#1508, #1576)
- **The per-file symbol budget is spent on the question.** When a retrieved file held more symbols than its budget, hydration kept a prefix sorted by line number, so the bigger the file the stronger the top-of-file bias and the less reachable the symbol you asked about. Symbols are now scored against the question's terms for selection. The kept slice is still served in document order. (#1597)
- **`get_why` ranks by question vocabulary and refuses when nothing matches.** Search mode answered every question: over twenty questions on an indexed repo, none of the eight unanswerable ones was refused, each costing 11k to 15k characters to answer wrongly. Ranking is now the share of the question's vocabulary a record carries, weighted by term rarity, and a miss returns an empty list with the tool that fits the question's shape. Average response 12,492 characters down to 5,624. The right record now ranks first on all twelve answerable questions, up from none. (#1558)
- **`get_why` search payloads roughly halved.** Natural-language mode was the only one of the three with no caps at all, at 27k to 35k characters against a 32k transport budget, so two of five probe questions were over the ceiling outright, where the host rejects rather than truncates. One embedding per call instead of two, `affected_files` clamped to a head plus a total, restatements collapsed on cited evidence, and three whole records rather than eight thinned ones. Now 12k to 16k characters, latency down 11% to 29%. (#1555)
- **`get_why` answers about its `--target` / `targets`.** It chose the health dashboard before looking at the targets, so naming a file and asking nothing returned the repo-wide summary. Targets are read first now: one target is path mode, several get a card each. (#1566)
- **The multi-repo dashboard puts the repositories back at the centre.** Four metric cards over a repo list becomes a repository list as the subject, ordered by attention rather than last write, each row carrying its health band and a marker only when something needs doing. The aggregates that legitimately add moved to a stat ribbon. `file_count` was counting symbol nodes as files, about 10x too large, and is fixed at the source, so the chat empty state gets it too. (#1577, #1578)
- **The workspace pages are on the design language.** All four are server-rendered again with filter and selection state in URL params, so a filtered view is linkable. Figures that summed things which do not add were deleted rather than restyled: an averaged coupling heuristic over a truncated list, an "Unmatched" count computed over one page of 200 rows and presented as a workspace total, a breakdown whose sum equalled the total it broke down, and an unweighted average of per-repo coverage percentages. Contract figures now come from the diagnostics endpoint, which knows the denominators, and the table states its bound. (#1582)
- **The VS Code extension is on the current design language.** Two local `scoreColor` copies called anything at or above 7.5 healthy-green while the canonical bands start at 8, so a file's score printed green beside a map colouring it amber. Both copies are gone. High-contrast themes now get a real high-contrast treatment instead of falling through to the ordinary ramps, and the health, settings, decisions and risk views take the shared components. (#1575)
- **`--no-editor-setup` means no editor setup.** It gated only machine-wide MCP registration, so there was no combination of flags that indexed a repo without writing `.mcp.json`, `.claude/CLAUDE.md`, `.vscode/mcp.json` and `.vscode/extensions.json` into it. Now one switch means one thing: only `.repowise/` is touched. `init` also ends by naming the files it actually wrote, sourced from the writers rather than a hardcoded list. Note for CI: `REPOWISE_SKIP_EDITOR_SETUP=1` is the same switch, so a harness exporting it to protect a global config will stop getting `.mcp.json`. (#1572)
- **The init screens are one system.** The banner's wordmark ran a colour ramp whose jitter exceeded its own gradient, so what shipped was orange static in the first thing a user sees. It is one brand colour now. Four panels that are not objects you act on lose their border, the palette is closed to the declared colours, and the completion panel names two commands instead of up to seven. (#1571)
- **Generation progress splits paid work from free.** One bar counted 4,229 items where about 4,134 were template renders and 95 were model calls, so it reached 97% in minutes and then sat there, indistinguishable from a hang. Each cost tier gets its own total, the free bar finishes and hides, and persistence gets a bar instead of an indeterminate spinner. (#1599, #1607)
- **Degraded phases are visible on a normal run.** The CLI pins core logging to ERROR unless `--verbose`, so whole phases could fail (the parse pool dying back to one process, a checkpoint not reaching disk) and print nothing. Those now render through the progress channel, including on a non-TTY, and are collected into `state.json` as `degraded`, the way `update` already reports one, with key-shaped text redacted on the way in. (#1599)
- **The Codex hook stopped waking on every prompt.** It registered `UserPromptSubmit` with no matcher and returned the same static note `SessionStart` had already delivered, so an agentic turn paid one process start per prompt to repeat a block the agent was already holding. The event is retired, and existing installs are migrated rather than left behind, since the merge into `.codex/hooks.json` is additive and reinstalling would have repaired nothing. (#1549)
- **Hotspot health has one owner, computed live.** Six surfaces answered the question and disagreed. `get_overview` and `repowise status` averaged the top 25% of files by size rather than churn, differing from the persisted KPI on all 42 local indexes with a median gap of 2.67 points of 10. Two dashboard routes read a snapshot that goes stale after `repowise update`, and `get_health` did not return the number at all. `None` now means "this repo has no hotspot files", kept distinct from a low score. (#1552)
- **The edge-type vocabulary is true and enforced.** The `EdgeType` declaration was decorative: it listed three types nothing emits and omitted four that producers emit constantly (6,750 rows), and thirteen modules each kept a private set written against it. Shared families replace all of them, with an AST guard test over every call site. Cycle detection gains 6,153 edges it never matched, and a file reachable only through a dynamic import could previously be reported dead. (#1516, #1518)
- **Dependency answers read dependency edges.** `graph_edges` also holds containment (`defines`, `has_method`) and the temporal `co_changes` relation, and several graph reads loaded every row and presented the result as dependencies. 43.7% of the rows the file-dependencies panel read were neither, and 34,570 of this repo's 34,808 symbols listed their own declaring file or class as a caller. Each site now names the view it wants, and degree counts are scoped to whatever is displayed beside them. (#1531)
- **"Can execution start here" has one owner, decided at ingestion.** Four implementations disagreed, so one file could lead the orientation list and be absent from the curated one. The flag is also what exempts a file from dead-code detection, so a name-only guess was a file nothing could ever report: 1,151 flags are withdrawn across 42 indexes and exactly one file becomes a new dead-code candidate. Entry-point lists on three remaining surfaces now use the shared ranking rather than PageRank, which rewards fan-in and floats a package-root barrel above the real front door. (#1533, #1547, #1556, #1559)
- **Workspace contracts are read from the parsed index.** Extraction re-derived route tables with regexes after ingestion had already parsed the same files. Python HTTP providers now come from the symbols ingestion produced, so a route cannot be picked up from a comment or lost for having an empty path, and the regex dialects stay reachable for every language with no AST tier. Five extractors that each walked the repo now share one walk: 45.2s down to 14.7-17.9s, 13,889 file reads down to 3,216, contracts and links unchanged. (#1581)
- **HTTP client wrappers are confirmed from the parse, not from their names.** The JS dialect decided a call was a service call by matching the callee's name against `fetch|request|http|api|ajax|rest|rpc`, which invents contracts for functions merely named that way and misses every wrapper that is not. A symbol is now confirmed by its body issuing a sink call, within a 2-hop budget. Measured on one frontend API layer: 12 contracts to 162. Unresolvable paths are counted and reported, never guessed. (#1585)
- **A workspace commit re-extracts only the repos that changed.** `changed_repos` was accepted and never read. Reuse is validated rather than trusted (carried forward only when the alias is unchanged, HEAD matches the stamp, the tree is clean and the contract config is unchanged), and every way of disagreeing resolves toward re-extraction. Cross-repo analysis and contract extraction now run concurrently, and each of the five phases logs its duration. (#1596)
- **Truncated lists carry their true totals** for cycles, co-change pairs and per-edge evidence, after the architecture score turned out to be computed from a capped cycle count, so 500 cycles scored the same as 50. Conformance and breaking-change reports are stamped when they run, so a report that never ran no longer renders as a clean bill of health. Contract extraction reports its coverage from counts it was already recording. (#1591)
- **Dead-code confidence tiers have one owner.** Eleven more literal `0.7`/`0.4` comparisons and caps now read the shared constants, with a guard test outlawing the shapes that kept recurring. Behaviour-preserving, measured over 42 indexed repositories: 3,930 findings both sides, none moving. Risk factors also render as words rather than API slugs, so a tooltip reads "configuration, runtime-loaded web asset" instead of "config, asset". (#1561, #1574)
- **`get_answer`'s internals are split into named modules.** `answer.py` had grown to 2,805 lines doing every job in the tool at once. Pure moves: no logic, ordering, threshold or payload change. (#1510, #1584)

### Fixed

- **Inline decision markers stopped matching ordinary prose.** The keyword matched case-insensitively, so a wrapped sentence beginning "# decision: ..." and a test label reading "# Rejected: nothing to extract" became the only two "decisions" a fresh index of this repository produced, one of them a mid-sentence fragment attached to twenty files. The keyword is now capitalised like `TODO:`. Markers past the fifth in a file were also silently dropped, and a source that failed was indistinguishable from a source that honestly found nothing. Failures are now reported as warnings naming the source and the error. (#1610)
- **A confidence note is written from the reason the grade was reached.** `get_answer` could return `confidence: high` beside `retrieval_quality: weak`, with a note claiming "clearly dominates (dominance ratio 1.00x)", a ratio of 1.00x being a tie. Dominance had two owners that disagree in a real window. There is one now, so `retrieval_quality: weak` means exactly "not dominant", and each note quotes the test that actually earned the grade. Answer-grounding can no longer earn `high` over a weak retrieval unless you opt in. (#1605)
- **A stray delimiter no longer hides the rest of a file.** The masking walk behind `withheld_symbols` discarded and re-walked when a template literal was left open at end of file, but triple-quoted runs and block comments got no such treatment. Either one left open masked every line below it, and the note then described a smaller set of withheld symbols than the payload had withheld. A stored symbol end line is also clamped to the live file, so a body served whole can no longer be flagged as cut with a continuation pointing past the last line. (#1580)
- **`repowise search` and `ask` stopped falling back to keyless embeddings** on a repo indexed with a real key in the same shell, reporting no semantic search against an index full of 1536-wide vectors. The CLI had no key resolver at all and never read `<repo>/.repowise/.env`, the file `init` writes the key to, so the MCP server and the CLI disagreed about the same directory. The degraded message also names where it looked, instead of telling a user to set a variable whose value is already on disk. (#1599)
- **`init` persists an API key supplied through the environment.** Key persistence hung off the interactive key *prompt*, so `repowise init --provider openai --yes` with `OPENAI_API_KEY` exported indexed fine but left no `.repowise/.env`, and `repowise mcp` against that repo answered `degraded: "no-llm-provider"`. The key that indexed the repo is now saved, with a one-line notice naming the file. `--no-save-key` (or `REPOWISE_NO_SAVE_KEY=1`) opts out, and answering No at the prompt still wins. Covers `init`, workspace init and `workspace add`. `.gitignore` is written *before* the key, so a failure cannot leave a committable secret. (#1595)
- **The chat window never streams with nothing to show.** A turn that ended without a terminal event left the composer spinning forever with no reason printed. The stream never settled when the reader ended early, an unknown-conversation failure was sent without the `type` field the client switches on, and the send never handed its `AbortSignal` to `fetch`, so a cancelled turn left the POST running and a database session open. (#1586)
- **The `module_page` prompt stopped tripping its own validator.** It asked the model to ground claims in "the supplied material", which is a literal match for one of the artifact rules, so a page that echoed the instruction's own wording was rejected and thrown away with its tokens already spent. Reworded, with a guard asserting no prompt or template matches any rule, plus one corrective re-ask so a paid-for page is not simply lost. Symbol spotlight pages also bound their importer list: three pages on this repo were about 46.6k characters, of which 45.7k was the list, so the tail fell outside the embedding window and the pages were searchable only by their first few hundred importers. (#1606)
- **The knowledge graph rebuilds when the builder changes**, not only when the graph does. The fingerprint measured node, edge and community counts, which hold still while the builder changes underneath them, so a store kept serving an artifact built before six edge types were admitted. (#1592)
- **Truncated agent-facing lists are ranked before they are cut.** Stale decisions, proposals awaiting review and ungoverned hotspots were appended in scan order, so "your ten stale decisions" meant ten arbitrary ones. Six generation lists and the transitive-affected walk behind `get_risk`'s `will_break` had the same shape. (#1590, #1598)
- **Change-risk correctness.** Uncommitted work is scored when the tree is dirty instead of silently scoring the previous commit. Merge commits are read against their first parent, so `impacted_tests` and `prior_fixes` are no longer empty for a merged PR the same response says touched several files. A failed author lookup reports unknown rather than `0`, which the model priced as risk. The 200-commit baseline walk is now cached whole rather than per scored commit, and self-exclusion works for the default `HEAD` target, which had been ranking against itself. (#1583, #1589)
- **Cached pickles under `.repowise/` are HMAC-sealed before unpickling**, so a crafted cache file committed into a repository cannot execute code during `init` or `update` (CWE-502). (#1439)
- **`REPOWISE_EMBEDDING_TIMEOUT` is honoured by every embedder.** Only the Ollama one read it. openai, gemini and openrouter hardcoded 10s, and an expired batch is not retried: it is reported once as "N/N items failed to embed" while the run exits 0. A malformed value now warns and falls back instead of silently downgrading the run to a keyless 8-wide store. (#1475)
- **`kimi-for-coding` models are sent `temperature=1`**, the only value that endpoint accepts. Every request through the kimi provider failed validation, which surfaced in `repowise generate` as every page silently falling back to a structural stub. (#1323)
- **A failed schema-reconcile statement stops being permanent.** The walk aborted on the first raise, so every table ordered after the victim was never reached, on that call or any later one, and 13 read paths swallow the error, so a half-migrated store was reached silently. It continues past a failure now and re-raises once the walk is done. (#1509)
- **`repowise update`'s up-to-date self-heal runs under the single-flight lock** (#1528), and the lock never reports `acquired` with no lock file on disk (#1524). The sync baseline is preserved when HEAD is unavailable (#1521).
- **`repowise dead-code --format json` emits valid JSON.** The banner and two notices went to stdout ahead of the payload. (#1441)
- **C# generic type arguments resolve** (#1550), and literal dynamic-import namespace use is tracked (#1548).
- **Chat tooling matches the real surface.** The `get_context` include schema listed blocks that do not exist and omitted two that do (#1477), and `get_why` results render in chat artifacts instead of coming back blank (#1478).
- **Dismiss dismisses.** The button sent `deprecated`, which is re-derived on every re-extraction, so dismissing a wrong proposal did not stick, which is the one thing the button exists to prevent. The shared status union gained `dismissed`, and the table gained the filter without which a dismissal is a one-way door. (#1567)
- **Docker standalone output path** corrected for the monorepo layout. (#1562)
- **`python -m repowise.cli.main` runs** instead of exiting 0 with no output. (#1591)

### Documentation

- `docs/layers/CHANGE_RISK.md` rewritten around fix history, including what the keyword-based fix classifier misses and why the ranking set is a falsification test rather than an accuracy estimate. (#1593)
- Dead-code docs corrected against the code: the git-age ladder has six rungs and was documented as three, `safe_to_delete` omitted its runtime-load condition, the risk-factor token list read as complete while being partial, and `ARCHITECTURE.md` promised a whitelist file no code reads. (#1559, #1574)
- Workspace docs drop a pom.xml scanning claim and an OpenAPI field-level breaking-change claim, neither of which is implemented, and document the socket extractor. (#1591)
- Object Pascal added to `docs/layers/LANGUAGE_SUPPORT.md` with its known gaps. (#1353)
- A dead-code CLI walkthrough, corrected hook event lists, and the editor-files architecture doc brought back in line with the current queries. (#1549, #1556)

### Dependencies

- Adds `tree-sitter-pascal` for Object Pascal support. (#1353)

---

## [0.42.0] — 2026-08-13

repowise shipped real integrations for exactly three agents — Claude Code, Codex and VS Code — with no cheap way to add a fourth. The wiring lived in four unrelated places, each carrying its own copy of what a given agent needs, and the two plugins had already forked from one another with nothing detecting the skew. This cycle puts every agent behind one descriptor seam and then proves it by adding three: **Cursor, OpenCode and Hermes**. A new `repowise agents` command group lists what is wired, adds and removes targets, and prints a config snippet for hosts we write nothing for. A support tier is derived from what a target actually wires rather than declared, so the docs cannot claim more than the code delivers.

The seam paid for itself immediately in a place that was not the point of it. Removal was never really implemented: `init` wrote to six surfaces and nothing took them away, so uninstalling repowise left MCP servers registered, hooks firing, and instruction blocks in files repowise did not own. There is a `repowise uninstall` now, and every removal path reports what it actually removed instead of calling a half-refused removal clean.

The other theme is a group of correctness fixes in ingestion and retrieval that all have the same shape: a signal was being read as something it is not. Co-change partners were being served as dependency edges, so "what does this import" answered with files that merely change alongside it. A TS/JS binding was classified by its name rather than its value. Large source files were dropped silently. Dead code called runtime-loaded web assets safe to delete.

### Added
- **`repowise agents`.** Lists every agent repowise knows, with its support tier, whether it looks installed, and every place it is currently wired — including duplicates, because "configured" is the wrong answer when the truth is "configured three times". Subcommands `add`, `remove`, `refresh` and `print-config`, each with `--format json` that reports what changed, so an agent can wire itself up and read back the result. (#1448)
- **Cursor, OpenCode and Hermes as agent targets.** Each is a descriptor file and a registry line. Cursor writes `.cursor/mcp.json` (it does not read `.vscode/mcp.json`) plus a rules file; OpenCode and Hermes read the same host-neutral `AGENTS.md` that Codex does, so the instruction block is shared rather than owned. Hermes also writes YAML and registers into its toolset list. (#1457, #1459, #1460)
- **`repowise uninstall`.** Removes repowise from everything it wired: MCP registrations, hooks, instruction-file blocks, and the local index, in either scope. Shared surfaces are left alone while another agent still reads them, and the user is told who. (#1467)
- **Codex slash commands**, built from the same shared source as the skills rather than forked a second time. A Codex plugin manifest has no slot for commands, so they install from package data into `~/.codex/prompts/`; Claude Code gets its commands from the plugin and never from `init`. Both hosts' skills now render from one body, with a drift report when they diverge. (#1450)
- **A generated agent support matrix** in the docs, built from the registry so it cannot drift from the code. The published MCP tool count is generated from the same place — six artifacts had been asserting a number that was wrong. (#1455)

### Changed
- **The three existing integrations were rewritten onto the new seam, not extended.** Claude Code, Codex and VS Code all resolve through one `AgentTarget` protocol with composable format helpers rather than a base class, because the targets genuinely differ: Codex writes a TOML server table, a TOML feature flag and a JSON hooks file for one install; VS Code writes two JSON files, one of which may carry comments. Every file the old path wrote is reproduced byte for byte. (#1446)
- **When a host-managed plugin is already registered, direct wiring stands down.** Claude Code is reachable both through its plugin and by repowise writing the config, and the host merges both without complaint — leaving two process spawns per matched tool call and a duplicate set of tool schemas resident in every session. Measured on a live machine: three repowise MCP servers at once, roughly 36 tool schemas for one product. (#1446)
- **A dead-code CLI walkthrough** was added to the examples. (#1384)

### Fixed
- **`get_risk` and `get_context` no longer serve co-change partners as dependencies.** Both read a graph edge set that mixed the two, so a file that merely changes alongside another was reported as importing it. Dependency edges are read on their own now. (#1462, #1470)
- **Large source files are indexed instead of silently dropped**, and when one genuinely is too large to parse, the run says so and counts it rather than leaving a gap the user cannot see. (#1443, #1237)
- **TS/JS bindings are classified by their value, not their name.** A `const` whose value is a call was not indexed at all, and a binding was typed from its identifier, so factory results and configured instances went missing from the graph. (#1468, #1469)
- **TypeScript workspace members resolve from `pnpm-workspace.yaml`**, not only from the `package.json` `workspaces` field, so pnpm monorepos link across packages. (#1454)
- **Files are decoded as UTF-8 rather than the platform locale**, which on Windows silently mangled any source with non-ASCII bytes. (#1466)
- **`get_answer` caps its confidence when a cited symbol body was truncated**, instead of reporting high confidence on a partial read, and validates the withheld `symbol_id` it hands back. It also stops reading backtick strings as code. (#1444, #1445, #1451, #1461)
- **Reachability is answered in one place.** Two callers each had their own notion of "is this file reachable", so the overview page could contradict the dead-code pass about the same file. Barrel files also rank down rather than dominating. (#1487, #1464)
- **Dead code stops calling runtime-loaded web assets safe to delete**, and the CLI now indicates when low-confidence findings are being hidden rather than presenting a filtered list as the whole answer. (#1463, #1434)
- **Deterministic structural wiki pages are localized.** With `--language` set, the model-written pages were translated while every structural page stayed English, so a non-English wiki was mixed. (#1092, #1102)
- **Orientation entry points rank the same way on every surface** — the CLI, the MCP tools and the web UI each had their own ordering. (#1488)
- **Concept pages stay local and are named for the directory they are actually under.** Unrelated top-level directories could be merged into one concept group, which then took a name from neither. (#1280, #1465)
- **`repowise mcp` keeps an older index readable** instead of failing on the first query, and caps confidence on a truncated symbol lookup. (#1458)
- **MCP tools emit `index_behind` and `embedder_degraded` when false**, not only when true. A caller cannot distinguish "not degraded" from "this build does not report it" when the key is simply absent. (#1449)
- **A failing CLI command names the real error** rather than a generic wrapper, and pytest runs are flagged. (#1447)
- **Webhooks match repositories by normalized URL** instead of a `contains` prefix, which matched the wrong repo when one name was a prefix of another. (#1440)
- **The test suite no longer repoints the developer's global editor config.** Running the suite could overwrite the real `~/.claude` MCP registration, breaking the developer's own tooling. (#1481)

### Documentation
- The documentation layer has its own reference, with the wiki style guidance folded into it. (#1493)
- Claims that contradicted the code were corrected across the reference docs, and the long ones were made navigable. (#1479)

### Plugins
- Claude Code plugin at 0.42.0; Codex plugin to 0.5.0 — both skill sets now render from one shared source (#1450).

### Internal
- The internal prerelease publish is idempotent, so a re-run no longer fails on an already-published version. (#1494)

---

## [0.41.0] — 2026-08-11

This cycle closes the gap between what an agent can ask over MCP and what it can ask from a terminal. `get_answer`, `get_context`, `get_symbol` and `get_why` had no CLI form at all, so anyone driving repowise from a shell or a CI job could search the wiki but could not ask it a question, read a triage card, pull one verified symbol body, or ask why the code is shaped the way it is. Those four are commands now. `search` and `risk` were rebuilt as thin adapters over the same tool functions, so a CLI answer and an MCP answer are the same answer, and thirteen more commands gained a machine-readable mode under one flag spelling.

The other theme is `get_health`, taken end to end by following its own advice as a sequence rather than testing calls one at a time. That found seventeen defects across three passes. The single call `directive.plan_via` told an agent to make returned 60k characters and failed the MCP token cap outright; `only` projected away the `unresolved` block, so a mistyped target went silent again; and one documented call shape took thirteen seconds.

Alongside those: importing one MCP tool no longer costs all seventeen, four tool blocks stopped returning payload the caller already had, and two memory regressions on the indexing and generation paths are fixed. Three authentication holes in the local server are closed, including one on a route that had none.

### Added
- **`repowise ask`, `context`, `symbol` and `why`.** The four highest-value MCP tools now have CLI forms. They are not reimplementations: a registered tool is a plain `async def f() -> dict`, so each command awaits the same function the MCP server serves, over resources built by the existing tool bridge. (#1411)
- **`--format table|json` across the agent-facing commands.** Four flag names used to mean the same thing (`--format`, `--json`, `--output`, `--progress`) and 54 commands had no machine-readable mode at all. Thirteen gain one: `status` (single-repo and workspace), `costs`, `saved`, `corrections`, `whats-new`, `decision list/show/health`, and `coverage status`. Three that already had a machine mode under another name fold onto `--format`, with the old flag kept but hidden so existing scripts keep working. `--progress` is an event stream rather than a result payload and is untouched. (#1407, #1402)
- **Five plugin slash commands** for the new adapters: `/repowise:ask`, `/repowise:context`, `/repowise:symbol`, `/repowise:why`, `/repowise:export`. (#1428)

### Changed
- **`repowise search` and `repowise risk` are adapters over `search_codebase` and `get_risk`.** `search` carried its own retrieval: an FTS query, a LanceDB query, a LIKE over `wiki_symbols`, and a workspace fan-out that fused them. The tool already does that and does it better, fusing the full-text and vector legs through RRF instead of picking one, honouring per-repo excludes and tombstones, and routing an identifier-shaped query to the scored symbol index. Both halves of the public contract are preserved, including `--mode fulltext|semantic|symbol`. (#1412)
- **`get_symbol` is presented as a follow-up rather than a place to start.** Where a short capability list names it beside `get_answer`, agents spend most of their retrieval calls on it and finish having made more tool calls than an agent with no tools at all, because a per-symbol tool supplements navigation rather than replacing it. Given the same surface with no such list, they reach for `get_answer` instead. The tool stays registered and stays in the lean profile; what changed is what four surfaces say about it. (#1427)
- **Importing one MCP tool no longer imports seventeen.** The package imported all 17 tool modules at package import, and through them the health analysis, onboarding generation, FastMCP and sqlalchemy. A leaf import initialises its parent package first, so importing any one tool paid for all of them: about 2.5s, against about 290ms for the entire CLI. Tool modules resolve lazily now, through the module's existing PEP 562 `__getattr__`. (#1409)
- **Four tool blocks stopped returning payload the caller already had.** A tool result is new text entering an already-cached conversation, so it is billed at the cache-write rate and paid again on every result. `get_context` serves the symbol card again instead of auto-upgrading file targets to a skeleton, which measured 2,171 characters against 6,585 on one mid-size library. Each cut is conditional, so nothing is lost. (#1426)
- **The parse pool is capped instead of sized from the host's cores.** It asked for one spawned worker per CPU. Every worker is a fresh interpreter that imports the repowise stack and builds tree-sitter `Language`/`Query` objects, a flat cost of about 50 MB of private memory each, measured at 51.0 MB/worker on PowerToys and 49.2 MB/worker on hugo. Peak memory was set by the machine rather than the work: 32 workers held 1.57 GB where 8 hold 0.46 GB. Nothing was bought with it. With 16 cores free, 8 workers parsed PowerToys faster than 16 did. (#1410)

### Fixed
- **Seventeen `get_health` defects**, in three passes. `directive.plan_via` named a call that returns 60,296 characters and fails the MCP token cap, and now names the projected form at 13,503. `only` projected away `unresolved` and `known_modules`, so a typo'd target returned an empty list that read as healthy. The performance dimension had no ranking key, the refactoring surface offered clone plans that could not be extracted, and plan ranking clustered on one file. On the cost side, `graph_nodes` had no index covering `node_type`, its most-issued predicate, so every "all the file nodes" read scanned 36,480 rows to return 3,449 (new composite index, Alembic 0051). (#1413, #1414, #1415)
- **`get_context` resolves the symbol ids `get_symbol` hands out.** `get_symbol` normalises the separator between qualified-name segments across `.`, `::` and `/`, since which one a caller writes is a fact about their language. `get_context` split on `::` only to gate the file path and then matched verbatim, so the dot form returned "Target not found" and the `::` form failed worse: it matched the graph-node rung, was typed as a file, and returned a card describing the method as empty. (#1435)
- **Generation no longer holds a second copy of the repository's source for the whole run.** Every `FilePageContext` carried `file_source_snippet`, the decoded source trimmed to a 48k-token budget, which for almost every file is the entire file. No template, prompt or caller ever read it. Contexts stay alive until the run ends, so a large repository could exhaust memory during generation. (#1403, #1394)
- **Piped and CI output stopped truncating result paths.** Rich sizes a non-terminal console at 80 columns, so a pipe got the most aggressive truncation of all while a human in a wide terminal saw everything. `search` rendered paths as `packages/core...`, which cannot be opened or grepped. Width is now resolved once where the shared consoles are built. `COLUMNS` still wins when set. (#1402)
- **`export --format json --full` crashed** with an `AttributeError`, reading a `finding_type` attribute that does not exist on `DeadCodeFinding`. (#1416)
- **The chat tool surface matches the registry it actually has.** Labels, suggestions, system prompt and artifact contracts still described tools that were removed or were never wired, including `get_architecture_diagram` and `get_dependency_path`, and `get_change_risk` results were not summarised. (#1417, #1418, #1419, #1420, #1421, #1423, #1424, #1425)

### Security
- **Webhook authentication fails closed.** `REPOWISE_GITHUB_WEBHOOK_SECRET` is required rather than recommended, the secret is read per request, and a request without a signature is refused with 403. (#1408)
- **The MCP server binds explicitly on the HTTP and SSE transports** and warns when an exposed bind has no `REPOWISE_API_KEY`. (#1406)
- **The repo health coordinator route requires an API key.** It was reachable off-host without one. (#1405)

### Documentation
- Command walkthroughs for `search`, `decision`, `wiki-export` and hooks sync. (#1385, #1386, #1387, #1388)
- The architecture docs' MCP inventory matches the 11-tool default surface, and the chat tool registry matches the live 7-tool set. (#1173, #1174)

### Dependencies
- mermaid 11.16.0 to 11.16.1 clears three advisories at once: prototype pollution in architecture diagrams, an infinite-loop DoS in XY charts, and a DoS in radar diagrams. The declared floor in `packages/ui` moves with it so a later resolve cannot land back on a vulnerable build. nanoid and brace-expansion move too, closing two more. (#1433)

---

## [0.40.0] — 2026-08-10

The big one this cycle is a new kind of record. Repowise already told you what the code is and what history says about it; it had no way to say what has happened to a repository: that this directory is a separate checkout, that a console script is shadowed, that this bug was fixed in this commit and nothing in its scope has moved since. Episodes are those records: dated, bound to the files they touch, derived from the walk and the git pass that already run, and checkable against git rather than decayed by a curve. They are read by `get_answer`, `get_why`, `get_risk` and `get_context`, and served over HTTP for the dashboard.

Alongside it: a keyless index stops ranking on vectors that carry no signal, which was roughly 40% of every search window; the health dashboard ranks by how much a file actually lost rather than by a score that clamps; `repowise watch` indexes the working tree, so it finally updates anything it watches; an incremental `update` prunes deleted files and can no longer strand the commits a failed step skipped; and the update path got about ten seconds faster on a large checkout.

Two authentication and file-access holes in the local server are closed. If you have ever run `repowise serve` on anything other than the default loopback bind, read the Security section first.

### Security
- **`repowise serve --host 0.0.0.0` no longer serves an unauthenticated API to the network.** The auth layer decided whether a request could skip the API key by reading `REPOWISE_HOST` to tell a loopback bind from an exposed one, and nothing on the CLI path ever set it. So the server bound the world while auth read "127.0.0.1" and let every request through, and the fail-closed branch for an exposed bind was unreachable code. The decision now comes from the request's peer address, so an exposed server fails closed however it was started and whatever the environment claims. Separately, the bundled web UI bound the wildcard while the API stayed on loopback: the UI rewrites `/api/*` to the API over localhost, so a UI on `0.0.0.0` handed the whole API to the network on the UI's port with every proxied request looking local, even at the default `--host 127.0.0.1`. The UI now takes the API's bind. One residual is known and warned about at startup: with an explicit `--host 0.0.0.0` and no key configured, that proxy still fronts the API. Setting a key makes the proxied path 401. (#1391)
- **`/file-content` serves only files the indexer recorded.** Its one guard was containment in the repository root, and `.repowise/.env` (your provider API keys) and `.git/config` sit inside that root, so any caller who could reach the endpoint could read them by path. It now applies the same three-table existence test `GET /files/{path}` already used, and denies `.git/` and `.repowise/` outright. Only those two directories are denied by name, because the traverser does walk other dot-paths and `.github/workflows/ci.yml` is a legitimately indexed file. Both callers, the web file page and the VS Code architecture view, only ever requested paths from the indexed file list. (#1393)

### Added
- **Episodes: a dated, scoped record of what has happened to a repository.** Three tiers. Structural facts are derived at index time from the walk that already runs, with no history, no transcripts and no API key: nested git repositories, an editable install shadowing a console script, configuration that changes what a command does, a tree that is not formatter-clean (#1317). Git episodes are bug-fix commits whose diff changes production code, each carrying the author's own account, the files that moved together, and a birth commit that makes "does this still hold" a question git can answer (#1324). Session episodes record each agent session in its own words, bound to the files its tool calls touched; they are per-machine and never leave it (#1325). A session outlives the transcript it came from, which agents prune on a schedule nobody sets, and episode bodies are full-text searchable (#1331). (#1325, #1331)
- **`get_answer` appends a recorded fact that disagrees with its answer.** It reasons from the code as it is now, so asked whether to run the declared formatter before committing, it reads a format target out of the build files and says yes, while the checkout's own record says the tree is not formatter-clean and a repo-wide run would produce a large unrelated diff. A scoped, still-current record now rides alongside the answer as a dated quotation. Three conditions, all required, or the payload is byte-identical to before. (#1318)
- **`get_why` answers "what happened here" from episodes,** beside decisions, git archaeology and rationale comments. `get_risk` and `get_context` carry an `episodes` count on their cards. Both surfaces serve only the tiers that describe the repository, so two people asking one question of one repo get one answer. (#1333)
- **Episodes over HTTP.** Four routes under `/api/repos/{repo_id}/episodes` (a paged list, grouped counts, a per-file lookup and a detail route), plus the TypeScript contract and API client. Only shareable tiers cross the wire, reading never creates the store, and no list route shells out to git. (#1346)
- **A decision is bound to the directories it governs, and its staleness is measured rather than modelled.** Module linkage was the first path segment of each affected file, so in a `packages/` layout almost every record claimed "packages" or "tests". Staleness is now the fraction of a record's files that have been committed to since it was recorded, not a formula fitted to one repository that needed 15 commits in 90 days before it moved at all. Records naming no file are counted separately as unscoped, because they cannot be asked the question. (#1322)
- **Unchanged re-reads collapse to a notice.** On PostToolUse Read, when the same range was served this session, no Edit came between and the bytes hash the same, the payload is replaced by a short line naming the earlier read. Opt-in via `hooks.read_reread`, default off. A file whose content differs is served in full plus a line saying it changed on disk and not through an edit in this session, which is something nothing else in the session can discover. (#1362)
- **A failed path is answered with the file the index says you meant.** On PostToolUseFailure, when the basename resolves to exactly one indexed file still on disk, repowise names it. Everything else is silence: an ambiguous basename, a directory target, a path in another checkout, a failure the agent already answered itself. (#1336)
- **`repowise saved` reports the net trade, and can report a loss.** It counted credits only and structurally could not show one. Debits are priced as they are actually paid: the CLAUDE.md block and the MCP schema are resident, so they are re-read on every API call, and the amplification is measured from real cache reads rather than assumed. Costs that local data cannot compute are named in the output rather than dropped. (#1362)
- **A Codex session transcript adapter,** on the harness-agnostic transcript layer the shared session code was refactored onto. (#1277, #1316)
- **Every embedding provider verifies the width of the vectors it gets back.** (#1305)
- **`/repowise:security` in the Claude Code plugin,** exposing full-history secret scanning. (#1107)
- **`repowise watch --index-only`,** so a watched docs repo does not mean a model call per save. (#1363)

### Changed
- **The generated CLAUDE.md / AGENTS.md block is about 30% smaller.** It is resident in the prompt prefix, so it is re-read on every API call rather than paid once; measured on one local corpus that amplification is 50.4x at a median 299 calls, which made the block the largest single cost repowise imposes on a session. Only the static half is cut. 5,788 of its 10,350 characters were identical in every repository repowise has ever indexed. Every section derived from your tree is untouched, all ten tool rows stay, and so do the argument shapes and the response fields an agent needs to call a tool correctly. (#1382)
- **The augment hook no longer fires on shell commands.** Across one 287-session corpus, Bash and PowerShell were 8,942 of 17,441 hook invocations (51%) and produced 64 emissions (0.7%). The cost is process start, paid before repowise reads the payload, so no gate inside the handler could avoid it. SessionStart already carries the freshness line; what is given up is a mid-session commit going unflagged until the next session. Installed machines narrow themselves without a re-init. (#1382)
- **The skeleton-nudge Read hook is retired.** It was the loudest surface in the system and it never changed behaviour: replaying 516 firings across 203 sessions, a structure call followed 11.4% of nudges against an 11.9% unconditioned base rate, and a second exposure read the same as the first. It was also answering the wrong reads. 53.3% of firings followed a ranged Read, which is advice to be more targeted handed to a read that already was. The replacement surface covers the case by serving the skeleton instead of describing it. (#1339)
- **The Decisions page says what it actually knows.** It led with the size of its review queue, so the largest number on screen pointed at unreviewed guesses. The confirmed count leads now. Conflicts is gone (it rendered from a table holding zero rows on every repository). Most-governed-files is gone (81% of the links it ranked belonged to proposed records, under a heading that read like established governance). Staleness is a count rather than a percentage, and a new "About this checkout" section carries the structural facts, which is the only part of the page that says anything on a repository indexed an hour ago. (#1352)
- **Health's `module` is the package boundary, not a graph community label.** The column held two vocabularies with nothing saying which one a row carried, and which one you got depended on which code path last wrote it. 41.5% of this repo's rows named a directory the file is not in. Go, Maven, Ruby, sbt, Clojure and Erlang monorepos get real package attribution for the first time, corrected in place on `repowise update` rather than requiring a re-index. (#1359, #1366)
- **Health findings split test material into its own bucket.** At the default limit a quarter of the most-read list described the test suite. `top_findings` and `findings` carry production findings, `test_findings` carries the rest, and the two totals partition the whole open set. Bucketed rather than excluded: a thrashing test suite is a real signal, just not the same question as where the defect risk is. KPIs deliberately keep test files, since tests score better than production code and dropping them would lower every repo's headline with no defect having been found. (#1361)
- **`get_health`'s directive stops promising a fix it does not have.** It names one file, states the dominant reason, and pointed at the refactoring block as the way to fix it. Nothing emits a plan for a coverage gap, which is the dominant cause on 10 of the 10 worst files here, so the directive named coverage and returned plans for complexity and duplication. It now carries `plan_addresses_reason` and, when false, a note naming the unaddressed cause. (#1358)
- **`repowise health` honours the config's exclude patterns.** (#1366)

### Fixed
- **A keyless index stops ranking on its own vectors.** Keyless mode is documented as full-text-only in four places and the code never implemented it: the no-key embedder's 8-dimensional vectors were fused into rankings at full weight. They carry no signal: every component is non-negative, so two unrelated strings score 0.750 cosine on average and never below 0.207. The consequences were measured: the relevance floor written to stop unrelated pages surfacing was cleared by 100% of them and never fired, about 40% of every `search_codebase` top-5 was noise, `get_answer`'s confidence was corrupted along with its ranking, and decision dedup merged an unrelated new decision into an existing one 100% of the time past 50 records. (#1378)
- **Two more keyless vector reads, and the confidence grade underneath them.** `repowise search --mode semantic` built its own store inline, so the first sweep never reached it and it rendered a window of nearest-neighbour noise with the full-text fallback sitting unused below. Separately, `get_answer`'s agreement lift required the full-text and vector legs to concur, which a keyless index can never do, so every keyless answer was graded by the gate that signal exists to correct. The symbol leg becomes the second opinion, on a stricter tie. Also: `--embedder ollama` against a stopped Ollama now says so instead of indexing silently without semantic retrieval. (#1380)
- **A keyless wiki stops warning about every page it has.** One confidence value meant two things, a page whose provider call failed and the deterministic page a keyless or `--no-prose` run renders on purpose, and the reader turned it into a banner telling you to verify the content against the source. On a keyless index that banner landed on the repository overview and every subsystem page, about pages assembled entirely from the parse, the import graph and git history. The two cases are separated, and the reader keys on the failure marker rather than the number, so an already-published wiki reads correctly without a re-index. (#1383)
- **`repowise watch` indexes the working tree, so it actually updates.** It fired an update on every save, and that update diffed `last_sync_commit..HEAD`, and on a repo you are actively editing there are no new commits, so the diff was empty by definition and every run printed "Already up to date". The watcher has therefore never indexed anything it watched. Also: events are filtered through the traversal blocklist so a build no longer means thousands of triggers, the files repowise itself rewrites can no longer make each update schedule the next, both ends of an atomic-save rename are considered, updates are serialised, the lock is released between runs, and reverted edits are undone in the index. (#1363)
- **A config change no longer advances the sync pointer past commits it never indexed.** `update` took the config-changed branch, re-scored health and returned early, skipping the git re-index, then saved `last_sync_commit: head`. The next run reported "Already up to date" and those commits' churn, ownership and co-change were lost until a manual `--full`. (#1360)
- **An incremental `update` prunes deleted files.** Deleting a file tombstoned its wiki page and left everything else: graph nodes and edges, metrics, symbols, health findings, dead-code and security findings, git metadata. MCP, search and the graph kept serving symbols for a file that no longer existed. Liveness is asked of things independent of the parse, so a transient read failure or an antivirus scan cannot look like a deletion, and a prune that would take more than half a table is refused and reported instead of applied. (#1377)
- **A degraded update no longer strands the commits it skipped.** Every persist step degrades into a report, then the pointer moved to head regardless, so a failed step took its commit range with it permanently. A failure now records a repair marker holding the previous pointer, and the next update widens its diff base back to it. (#1377)
- **Dead code persists the repo-wide verdict an update already computed.** The analysis was always repo-wide; its result was then filtered to the change set, discarding exactly the findings the change had just caused. Drop the last import of a module and that module becomes dead, and it is not in the change set. Widening the write alone would have made things worse, because the analyzer saw no git metadata for unchanged files and an empty record reads as "no commits in 90 days", so the fix also feeds it the stored per-file git fields. A dismissed finding is no longer resurrected. (#1389)
- **A cohesive Go package is no longer reported as a circular dependency.** Files in one Go package reference each other with no import statement, so several passes synthesise file-level edges to keep reachability honest, and cycle detection read those as dependencies. Never Go-only: every `foo.h`/`foo.c` pair was a guaranteed two-file cycle and C# partial classes formed an all-pairs ring. Cycles before to after: gitleaks 1 to 0, osv-scanner 16 to 0, syft 16 to 0, hugo 10 (largest 571 files) to 0. Existing indexes converge on `repowise update`. (#1354)
- **The worst-files lists rank by how much a file lost, not by a score that clamps at 1.0.** Thirty files sit at exactly the floor here, so every list sorted on score came back in path order and this repo's actual worst file landed at position 27, invisible at the default limit. Fixed once in the crud layer, so REST, MCP and hosted inherit one answer. A payload's headline and the list printed directly beneath it also disagreed about which file was worst; both now read one ordering. (#1342)
- **A file at the score floor can show movement, and the trend stops hiding improvements.** Snapshots persist the clamped value, so 28 of 29 floored files had a flat 1.0 series while carrying 9 to 13 points of deduction. The per-file movement list also sorted ascending and sliced to 50, making it the 50 biggest drops. Also repairs a migration chain that had two revisions declaring the same id. (#1364)
- **The health file drawer shows the line on file-level findings.** The `function:line` anchor was gated on a function name, so all 1,057 `error_handling` findings here rendered with no line and nothing to click: 34 rows on one file, identical in every visible field, because the drawer was withholding the one field that told them apart. A file-level biomarker that repeats now gets its own group, with a `capped` chip when the scorer is holding that category at its ceiling. (#1344)
- **`get_health`'s `only` projection stops dropping the totals.** `only=["modules"]` at `limit=50` returned 50 of 116 with nothing saying so, breaking the tool's own promise that truncation is never silent. `include` and `only` were also two vocabularies for the same blocks, so `only=["biomarkers"]` projected away the block it had just switched on; those are aliases now. `limit=0` means none rather than clamping up to one. (#1361)
- **`get_health` stops returning 4.7MB.** `include=["biomarkers"]` with no targets was the one ranked list with no cap, serving all 10,349 open findings, enough to overflow an agent's context and return nothing usable. Capped, with `findings_total` alongside. The dimension filter also ran over the finished response, so `include=["biomarkers","performance"]` came back empty while the total reported everything, which reads as "no performance risk in this repo". (#1337)
- **The coverage tab's badge no longer reports one module.** A single `limit` was capping two unrelated things, a page of files and the repo's covered-directory count, so the badge, which asks for one row because it wants a cheap response, was told the repo had one module. A `?file_path=` request had the same defect from the other side. (#1356)
- **The code-health map's churn lens paints the data it has.** It requested churn with no limit, so the server's default of 300 applied and about 1,700 of 2,000 nodes rendered as the no-data swatch. (#1342)
- **`hook stats`, `hook rewrite status`, `doctor` and the `saved` tip report what a surface is doing, not what it once did.** Retired surfaces kept computing rates over denominators that had stopped growing and rendered identically to live ones; they are labelled and dimmed, with a `retired` flag in `--json`. "Installed" was keyed on the hook command, so an entry whose matcher names a tool the agent has since renamed was reported as working; presence and reach are separate answers now and the verdict is three-state. `repowise saved` priced tokens at a hardcoded model while the costs endpoint priced the same ledger at the detected one, understating an Opus session by two thirds. (#1343)
- **An injected decision nothing could disagree with no longer counts as followed.** "Followed" was the else branch of the contradiction test, so a session that mined no correction handed out a positive verdict for free: 100 followed, 0 contradicted, and none of the 100 from a session holding any correction. Those rows settle with no verdict now. The supply side had the matching hole. The correction gate only fired on a message that opened with a pushback lead, finding 62 corrections across 436 transcripts against 260 for the same words at sentence start. (#1335)
- **A marker decision is verified against its own span.** Every marker in a file had its context joined into one blob handed to every decision drawn from that file, so a decision from marker 1 could be stamped `exact` against marker 3's words, and all of them inherited the first marker's line. Staleness was also scored against one run's change set, so on an incremental update decisions over untouched files went maximally stale for not having been touched: 13 of 19 rows here read 1.00 and now 1 does. Existing rows correct themselves on the next update. (#1315)
- **Three refactoring-plan fields stop reporting constants as measurements.** `suggested_name` was unconditionally null on all 854 stored `extract_method` plans while a sibling detector computed one; `blast_radius` was a hardcoded `{"callers_count": 0}` indistinguishable from a measured zero; and `suggested_site` mixed a graph community label with a filesystem path under generic keys, which is how a helper got named after the repository itself. (#1374)
- **`get_why` no longer wedges the session.** It never returned on a repo with git-tier episodes: 300s with no response and one `git rev-list` still running, with the agent's whole MCP session stuck behind it. Three git spawns did not pass `stdin=DEVNULL`, so under the stdio transport the child inherits the server's JSON-RPC stdin and can hold it open indefinitely, and the `timeout=` on each call is not the backstop it looks like, since on Windows the re-entered `communicate()` blocks on threads the killed child still holds. (#1395)
- **`doctor` stops reporting resume stubs as drift.** A stub awaiting `--resume` is held out of the vector store deliberately, because the vector is what tells the next `init --resume` the page is done, so counting it as a missing vector pointed people at `--repair`, which embedded the stub and silently burned the retry. It is surfaced on the row as "N stub(s) awaiting --resume" rather than hidden behind "in sync". Alongside it: `init` suggested `repowise decisions`, which is not a command, and blamed "provider failures" for pages its own artifact check had rejected; `dead-code` printed a raw dict; and `reindex` relabelled its progress bar "Indexing decisions..." on a repo with no decisions. (#1395)
- **`status` and `doctor` query the configured database when there is no local `wiki.db`.** (#1320)
- **A transient PyPI failure no longer pins the update check to "unknown" for 24 hours.** The failed result was written into the TTL cache, and the next call inside the window saw a cached `None` and skipped the live fetch. (#1330)
- **Resume no longer redoes finished phases on a repo with more than 100 pipeline jobs.** The completed-phase readers took the default row limit, so the set was silently truncated and prior git-history, centrality and decision-extraction work was repeated. (#1329)
- **The Read line count is correct for output ending in a newline,** which had been overstating by one and feeding both the nudge gate and the served-read windows. (#1328)
- **`get_risk` and `search_codebase` fit their schema budget again.** (#1334)

### Performance
- **The update prologue stops paying four repeated costs.** Assessing the store no longer imports lancedb on a keyless repo (1.517s to 0.036s), the tech-stack scan is memoized on the root manifests' stat signature instead of running twice per update, the deterministic page persist no longer rebuilds a page tree its caller rebuilds immediately after, and `init` stamps the re-score cadence it satisfied so the first update does not score every file again. A post-commit hook runs this path on every commit, so all of it was charged per commit. (#1376)
- **Lifetime churn folds onto an anchor instead of re-walking history.** Every update ran `git log --shortstat` over the entire history to move a number by one commit's worth of lines: 2.5s on a 2,216-commit checkout, 13.3s on a 9,335-commit one, growing with every commit anyone makes. Now 0.34s and 0.19s, byte-identical output, with a full re-walk every 100 commits to bound the drift that no ancestry check can see. Also fixes a timestamp parser that made a related backfill re-select the same rows forever, because strict ISO 8601 spells a zero offset `Z` and only `+00:00` was understood. (#1379)
- **An update stops rewriting the graph rows it did not change.** A one-file change arrived as tens of thousands of rewrites of rows that had not moved: 1.9s on a 15k-node checkout, 11.3s on a 63k-node one. Centrality values compare with a tolerance, because the kernels are not bit-stable across processes and exact equality would rewrite every node to store noise. End to end on a real store, 2.10s to 0.48s and 12.73s to 8.81s, with row-for-row parity. (#1381)
- **Dead-code analysis is four times faster.** All 579 never-flag globs were joined into one alternation with every branch tried at position 0, most beginning `.*`, which was 12.8s of an 18.1s cold analysis. Bucketing patterns by their end-anchored tail takes hugo from 3.10s to 0.72s and PowerToys from 25.29s to 5.71s, with identical findings. (#1389)
- **The public health badge stops reading the whole dataset to render one float:** 358.2ms to 19.8ms. `wiki_symbols` also gained an index able to seek by file, taking a 400-path lookup from 33.3ms to 11.7ms. (#1351)
- **`/health/overview` stops reading both health tables twice** (554.3ms to 376.4ms, and 2.2MB of snapshot JSON to zero), and `/overview-summary` stops loading the full retained snapshot history as entities to read three scalars off it. Both fixed a real defect on the way: the overview summary reported one recent fix for every flagged file, disagreeing with the health dashboard on the same page. (#1350, #1356)
- **`/health/refactoring-targets` stops shipping every file's findings.** It built and serialized findings for all 2,411 files before slicing to the limit, then discarded about 90%: 1.16MB to 190KB at the limit the UI sends. Both consumers sit behind a click and now fetch on demand. (#1345)
- **Query-time exclusion is compiled once per repo instead of per row,** 1,442ms to 3ms warm on a dashboard call, on a path shared by every MCP tool. `health_findings` gained the index it never had, taking a file-scoped lookup from 8.8ms to 0.1ms. (#1337)
- **A Glob that ripgrep gave up on is answered from the index** instead of costing twenty seconds and returning nothing. (#1362)
- **Read state is partitioned per session,** which fixes every Read hook surface being near-useless in subagents: a sidechain runs under a different session id from its parent, and the per-repo state file was keyed on session id and reset whenever it changed, so parent and subagent wiped each other's state on every Read. (#1362)

### Documentation
- **The README leads with the numbers.** Its strongest evidence was in its smallest text, under a headline that was a feature list. It now opens with the three results at size: first of six on finding the right files (0.876 against the next tool's 0.610 on a sealed 42-instance split), 31.6% fewer output tokens from the agent, and 97% fewer tokens to load a commit, plus the line that keeps what the strip cannot hold, including that we are the slowest indexer in the field. BENCHMARKS.md opens with a scoreboard and charts instead of forty-five lines of essay about why the page is built the way it is; the essay moves to the end as the closing argument. Nothing that runs against us was cut to make room. (#1365)
- **The benchmarks are rerun on a second and third agent harness, and one claim is retracted.** The page read the "agent used it" column as a design result about how well we had named our tools. It does not support that: repeating an identical Claude Code run with nothing changed on anyone's side moved us from 15 of 15 to 3 of 15, and a competitor from 13 of 15 to 2 of 14, while switching harness moved every tool in the field to 15 of 15. Adoption is a joint property of tool, harness and day, so every figure now travels with its harness and its date. Section 2 leads with the full 48-question Codex run, where every tool actually gets called. (#1319, #1321, #1340)
- **The code-health marker taxonomy is corrected.** The docs said 25 markers; the registry has 49 detectors across 52 marker ids, of which 26 feed the defect score, 20 the performance pillar, 3 maintainability only and 3 move no score at all. CODE_HEALTH.md is restructured from a reference manual into an argument, with a dozen figures corrected against live code and the bench data. (#1338)
- **The website's CLI and Claude-plugin pages match the shipped surface,** and a change-risk CLI walkthrough is added to the examples. (#1106, #1177)

---

## [0.39.0] — 2026-08-05

A small release. `get_answer` stops handing back bare file paths, the docs sidebar reads as an outline instead of a directory listing, and a store upgraded from 0.37.0 opens again instead of taking the whole wiki down with its search index.

### Added
- **`get_answer` names what each candidate file defines,** not just its path. Of 499 paths served across the 25 flow questions, 65 carried any content at all, and 89% of the agent's post-answer searches were the same shape: take a name the payload gave, go fetch the substance it did not attach. Each candidate now carries its declarations as `name:line` pairs, question-named symbols first, imports and private names dropped. Substance per served path goes from 0.130 to 0.864, for 10% of the response and no change to which paths are served or in what order. (#1306)

### Changed
- **The docs sidebar reads as a table of contents.** Every group on the top rung opens on load and nothing below it does, so a chapter that parents sub-chapters no longer expands wherever it sits and makes the tree read as a filesystem. The four-digit file corpus closes the tree instead of fronting it, selecting a page opens the chapters above it, and outline rows drop the folder glyph the no-icons rule had never reached. (#1312)
- **Mermaid diagrams scale to the column they are read in.** They rendered at natural size inside a scroll box, so the architecture map arrived clipped mid-subgraph behind two scrollbars. Width only, never above 1:1, with the scaled height reserved so a fitted diagram does not sit in a pool of empty space. Maximize still pans and zooms for the dense ones. (#1312)

### Fixed
- **A store upgraded from 0.37.0 no longer refuses to open.** 0.38.0 widened `page_fts` from three columns to five, and FTS5 cannot be altered, so the index is dropped and refilled from `wiki_pages` the first time an upgraded install opens the store. That rebuild refused whenever the index held more rows than `wiki_pages` could account for, and the error it raised told the reader to run `repowise doctor --repair` — which opens the store the same way, hit the same refusal, and died before repairing anything. `serve` and the MCP server died there too, so a search index took the entire wiki down with it. The excess rows are orphans: pages swept from SQL whose index delete never ran, because it runs after the commit, outside the transaction, on a best-effort path, and nothing ever reconciled them. An orphan answers a query in full and 404s when the reader opens it, so the rebuild discards it and reports the count rather than refusing. (#1309)
- **An interrupted sweep heals itself.** `ensure_index` prunes orphaned index rows on every open, which is the only thing that ever reconciled the two halves of the store; the residue could otherwise only grow. The scan is read-only and takes the write lock only when there is something to delete.
- **`doctor --repair` finishes what it was called for.** A failing full-text schema upgrade is reported and stepped over instead of aborting the repair, and orphans are deleted in one transaction rather than one per id.
- **`serve` and the MCP server start on a store whose index cannot be prepared,** falling back to whatever shape the index is in plus the vector arm, with a warning. Every other surface — the wiki, the graph, code health — was unreachable over a keyword index.

### Documentation
- **The benchmarks are rescored on the sealed half** after the retrieval gate fixes, and the token-efficiency figures are refreshed. The agent-loop result is reported as its own number rather than folded into the payload one, which measured a different thing. (#1307, #1308, #1310)

### Dependencies
- aiohttp 3.14.1 → 3.14.3, cryptography 48.0.1 → 50.0.0, gitpython 3.1.55 → 3.1.58, postcss 8.5.22 → 8.5.25, and the dev-only undici 7.28.0 → 7.29.0 and fast-uri 3.1.4 → 3.1.5. Clears the open security advisories against the tree. The cryptography floor moves rather than only its ceiling, so a fresh install of the published wheel cannot resolve back into the vulnerable range. (#1313)

---

## [0.38.0] — 2026-08-04

Four new languages, an orientation set rebuilt around what a reader actually needs, and a wiki that draws on the repository's own vocabulary instead of writing generic prose about it. Svelte and Vue reach the Full tier through a byte-preserving projection into TypeScript; HTML lands at the import tier; reStructuredText documents are read as reStructuredText rather than silently yielding nothing. Onboarding is six pages now, ending in a glossary built entirely from mined terms, and a directory that heads a subsystem gets a chapter even when it also holds files of its own. Full-text search was rebuilt on both backends after the query shape turned out to match 65% of the corpus on a median question. The agent hooks got measurement first and then acted on it: the Grep flood is replaced by its digest instead of ranked next to it, triage ranks the files the search actually matched, and the three hot index lookups dropped an ORM import that cost a second per hook fire.

### Added
- **Svelte and Vue at the Full tier.** A markup grammar locates the JavaScript-bearing regions of a single-file component, every other byte is blanked to a space with newlines preserved, and each markup expression is fenced by rewriting its two surrounding delimiter bytes. The result is valid TypeScript at byte-identical offsets, so the TypeScript queries, language config and all three health dialects apply unchanged; only region location differs per language. Components reach the file tree, git history, wiki and health for the first time. (#1221, #1232)
- **HTML at the import tier,** via `script src` and `link href`. HTML has no functions, classes or calls, so this ships an import-only tier and says so on every surface rather than minting symbols that do not exist. Template dialects are out of scope and that is stated: `{% extends %}` is plain text to an HTML grammar, and 744 of 749 measured dialect files produce no edges. (#1235)
- **reStructuredText is read as reStructuredText.** Every pattern in the document miner was markdown-only, so a repository that documents itself in `.rst` looked identical to one that says nothing. flask, requests and django were all in that position. Underline-length, directive bodies and roles are each handled, and `.rst` prose written under a `.txt` extension is picked up too. (#1238, #1247)
- **A glossary page, written entirely in the repository's own words.** House vocabulary (blast radius, change risk, co-change, distill) had no definition anywhere a reader could reach. Each row is a term, the repository's own defining sentence, where it is used and which document it was written in. The page has no model in its path at all: every cell is a fact the run already holds, so it costs no tokens and cannot hallucinate. (#1276)
- **Subject chapters.** A parent directory was disqualified from heading its children whenever it also held loose files of its own, because both pages would collide on one page id. That excluded exactly the directories a reader most wants a chapter for, and did it silently: nine of thirteen chapters suppressed on this repository, five of six on django. (#1282)
- **Perf dialects for Kotlin and C++, and a dataflow def/use dialect for C++,** which moves C++ to the Full tier with intra-procedural CFG, reaching definitions and Extract Method suggestions. C++ deliberately omits three markers other languages carry, each of which would be a guaranteed false positive there. (#1224, #1225)
- **A page says how far it can be trusted.** `confidence` had been a constant 1.0 on every page ever generated, so the reader's low-confidence banner had never rendered for anyone and retrieval could not weight by it. A wiki where a provider outage left hundreds of structural stubs looked exactly as trustworthy as a complete one. (#1213)
- **File pages and symbol spotlights name the questions they answer,** built from structure rather than prose, and emitted only where the page can actually answer them. (#1209, #1210)
- **Hook efficacy measurement.** Four of five hook surfaces wrote rows nobody read, and the Read surface wrote none at all, so a nudge could fire 500 times at a 0.2% action rate with nothing in the product noticing. Transcripts are replayed to pair each emission with the tool calls that followed it. (#1272)
- **Vocabulary mining with provenance.** Mined terms now carry their defining sentence, the document they were read from and every document that names them, which is what the overview, onboarding, key concepts and the glossary are built on. (#1233, #1241, #1242, #1249)
- **`get_answer` meters what one synthesis call costs,** and configurable synthesis evidence lets a deployment choose how much the model is shown. (#1180, #1227)
- **Retired page ids keep resolving,** and a retired page can hand off to the repository overview instead of dead-ending. (#1163, #1166)
- **The OpenAI embedder honours a configured output width,** for deployments pinning a narrower vector than the model's default. (#1254)
- **`update` names the orientation pages an index has never been offered** and points at `--full`, which is the path that can actually deliver them. An incremental run reads a changed-file slice, and every onboarding gate reads whole-repo signals. (#1288)

### Changed
- **The docs tree opens on the shape of the repository, not its contents.** Layers used to open by default, which was fine when a layer held a handful of children and wrong once layers grouped every module: roughly ninety module rows on the first screen, burying the layer names and the chapters alike. Layers start closed, the file corpus sits above the layer outline rather than under it, and an unclaimed module no longer poses as a layer. (#1184, #1185, #1186, #1187)
- **A search hit's snippet is centred on what the query matched.** It used to be the first 200 characters of the page, which on a generated page is the same `## Overview` opener every time: identical across thousands of hits, and never the passage that matched. (#1191)
- **The Grep flood is served as its digest, not ranked beside it.** Ranking a flood you also keep is a lens, not a saving; the digest was being added next to output the agent had already been billed for. It replaces the flood now, the same trade `distill` makes for shell output. Measured over real Grep payloads in 25 transcripts, the digest is 0.30 of the flood. (#1283)
- **Grep triage ranks the files the search actually matched.** It built candidates from name and path matches ranked by PageRank without ever reading the grep output; replayed over 1,899 real Grep calls, 83 of the 111 files it named were not in the grep results at all. (#1296)
- **The Read hook serves the skeleton instead of recommending it.** (#1275)
- **`distill` rewrites safe command chains instead of bailing on their shape.** `repowise saved --missed` reported 138,827 tokens over 478 runs in 7 days that never reached distill. The binding gate was a re-quoting rule that refused any command containing a quote, a dollar sign or a backslash, so `grep -n "a\|b" f.py | head` bailed on the quotes it obviously contains. What `--missed` counts is corrected alongside. (#1291)
- **Module pages name their own symbols** and stop opening with the same sentence as every other module page. (#1211, #1212)
- **The overview carries the architecture map, counts its packages** rather than describing them, and says what the repository does in the repository's own words. (#1164, #1246, #1249)
- **One recipe for every page vector,** with a page below the information floor getting no vector at all and pages that lose text at the embedding cap named rather than silently truncated. (#1200, #1192, #1203)
- **Running one CLI command stops paying for the whole import graph.** Three module-level imports were charging their dependency tree to every invocation, including every hook fire: `repowise --version` drops from 1,240ms to 150ms, and a silent hook from 965ms to 167ms. (#1273)
- **The three hot index lookups read through stdlib `sqlite3`.** Reaching the index from a hook cost a second, 95% of it a single import that none of the three plain SELECTs needed. (#1297)
- **A command no longer waits out its own telemetry POST.** (#1286)
- **Decisions rank a person above a document,** two sources that never landed are retired, and `get_why` path mode is bounded. (#1290, #1293)
- **Layers group the docs tree without needing a page to hang off,** and pages carry the provenance of the layer that groups them. (#1165, #1170, #1171)

### Fixed
- **Full-text search was asking for most of the corpus.** Both backends built a MATCH expression that could not retrieve, failing in opposite directions: SQLite OR-ed every token with a prefix wildcard, so on a 3,678-page corpus the median question matched 65% of it and one matched everything; PostgreSQL handed the raw question to `plainto_tsquery`, which ANDs every lexeme and therefore matched almost nothing. A tombstoned page is also dropped from the index now, and a page can be too thin to be worth indexing. (#1188, #1190, #1198)
- **The served skeleton never reached the agent.** `updatedToolOutput` is validated against the schema of the tool being replaced; the hook emitted a bare string where Read's output is an object, so Claude Code rejected it, used the original file, and the hook went on recording a saving. Every firing since the feature landed was a no-op that reported success. (#1278)
- **The answer prompt formatter halved every page excerpt it fetched,** page content is attached on every retrieval rather than only the weak ones, and decision vectors and pageless ids stay out of the answer. (#1168, #1169, #1183)
- **The first MCP tool call raced the lancedb import,** and the embedder API key is resolved from persisted config rather than the environment alone. (#1230, #1231)
- **The data-shape fast path reads the question, not the whole paste,** and the early returns hand back the ranked pool they already hold. (#1284, #1289)
- **Generation rejects pages that talk to the prompter,** refuses a structurally-keyed page with no key, drops sections a page cannot fill, tombstones a page whose file is gone, grounds flow narratives in exact source, and reads execution flows by the field names they have. A definition is taken to be prose the author wrote, not the markup below it. (#1172, #1201, #1202, #1206, #1207, #1208, #1229, #1245, #1248)
- **JS/TS extraction picks up unparenthesized single-parameter arrow functions,** and HTML intrinsic elements no longer pose as JSX component call targets. (#1215, #1217)
- **Python aliased imports parse correctly,** and a Node.js package `exports` wildcard may cross directory boundaries. (#1243, #1256)
- **`doctor` reconciles the store against the database again.** (#1196)
- **The Python perf dialect knows `pathlib` is filesystem I/O.** (#1269)
- **The betweenness pool is bounded, live update locks are kept, and a failed page is counted once.** (#1262)
- **Generation checks reach a normal run of `init` and `update`.** (#1178)
- **Docker:** `/data` is created before `chown`, the image moves to `node:20-bookworm-slim` for glibc compatibility, and `.gitattributes` enforces LF on `.sh` files so the entrypoint runs on a Windows checkout. (#1266, #1268, #1270)
- **The Stats punch card's UTC footnote describes rather than prescribes.** (#1240)

### Documentation
- The README carries media that renders on GitHub, a section and a picture for the PR bot, and cites the 21-repo health validation. (#1197, #1205, #1218)
- Published measured results with a comparison against real peers. (#1287)
- A `structurizr` export walkthrough in the examples and in the CLI package README. (#1175, #1176)
- CONTRIBUTING documents how to claim an issue. (#1263)
- The vocabulary overlap thresholds record what they actually measure. (#1181)

### Dependencies
- `tree-sitter-svelte` and `tree-sitter-html` are new, for Svelte and Vue respectively. There is no `tree-sitter-vue` on PyPI, and `tree-sitter-html` parses a Vue single-file component cleanly because `<template>`, `<script>` and `<style>` are ordinary elements to it.

---

## [0.37.0] — 2026-07-29

The release where the web UI got taken apart and put back together. Fourteen surfaces moved onto one design language: sections and hairlines instead of a grid of near-identical bordered cards, a sentence above every figure saying what the figure means, and a header row plus a key row on the pages whose canvas is the page. Overview, Docs, Commits, Contributors, Code Health, Coverage, Dead code, Chat, Settings, Decisions, Stats, Files, Refactoring, Knowledge Graph and Architecture all changed shape. The Architecture and Knowledge Graph canvases also got their marks named and their per-frame cost cut, and the docs page stopped downloading 38 MB of page bodies to draw a tree. Away from the UI there is a Structurizr DSL export, a first-run pass over interactive `init`, refreshed provider model defaults, and three MCP response fixes.

### Added
- **A Structurizr DSL export.** `repowise export --format structurizr` and `GET /api/graph/{id}/c4/structurizr` emit a [Structurizr DSL](https://docs.structurizr.com/dsl) model of the repository, with a download button on the Knowledge Graph page. `--standalone` wraps the model in a `workspace` block so the file opens on structurizr.com as-is, and `--components` includes the component level. Built on a new typed node-id module so ids carry their kind instead of being parsed by convention. (#1143, #1144)
- **`repowise init --no-editor-setup`** skips global MCP and hook registration, for CI, agents and throwaway installs that should not rewrite `~/.claude/settings.json`. `REPOWISE_SKIP_EDITOR_SETUP=1` is the same switch. (#1086)
- **`fields=summary` on `GET /api/pages`**, which drops `content` and `metadata` and returns a `content_chars` count in their place. `fields=full` remains the default, so existing callers are unaffected. (#1120)
- **A key row on the Knowledge Graph.** The arrows, the role dot and the health dot were three kinds of mark the surface never explained. All three are named now, the two dots no longer collide on colour, and a mislabelled verb in the backend map is corrected. (#1145)
- **`--verbose` and richer failure reporting on `init`.** A run that generated some pages and failed others said it succeeded; it now names the failures and points at `--resume`. (#1094, #1098)

### Changed
- **The web UI runs on one design language.** Cards at uniform weight are gone from Overview, Docs, Commits and Contributors, the contributor profile and commit detail, Code Health, Coverage, Dead code, Chat, Settings and Decisions. Sections and hairlines carry the hierarchy, a lede sentence carries the meaning of the number under it, and the dark surface ramp starts lower so reading columns sit on the base plane instead of in a trench between two lighter walls. (#1114, #1118, #1121, #1125, #1129, #1132, #1134)
- **The Architecture page steers one axis from one control.** Scope used to live in both the tab strip and a floating pill cluster, with "Communities" appearing twice on one screen. Tabs are datasets now (Map, Coupling, Packages, Symbols) and scope is a single labelled control in the section header. (#1146, #1150)
- **The Refactoring page leads with what the pile is.** The priority-by-effort quadrant plotted 120 of 1,819 plans, because 89% of plans rate small effort and the X axis was one column. It is replaced with charts whose axes actually spread, under a sentence that says what the plans are. (#1151)
- **The Files page opens on the map.** The treemap is the subject, so it goes first, with the four figures riding in the sentence beneath it rather than in a strip of equal-weight stat boxes above. (#1152)
- **The Stats page shows what no other page shows,** and the coding-rhythm punch card is reworked: marginal totals, a colour scale that means something, and no dead gutter on a wide viewport. On this repo the old chart made 853 commits look idle because every cell it could draw capped in the low tens. (#1091, #1096)
- **Provider model defaults refreshed.** Several shipped model ids had drifted from what the vendors serve, including a default for the recommended provider that was not a real id and only worked via the live `/models` fallback. The Anthropic and OpenRouter defaults were flagship-priced, contradicting the calibration guidance. Cost and temperature handling is corrected alongside. (#1137)
- **A first-run pass over interactive `init`.** A read-only walkthrough found 52 places where the terminal was confusing, contradictory or invisible; 49 are applied, plus five defects found reviewing the result. Phase reporting is one system now rather than two glued together. (#1139)
- **Documentation generation enforces completion** and filters the file dependency context it passes to the model. (#1010, #1011)
- **Python lint is gated in CI.** Ruff is configured to match how the repo is actually written, 508 accumulated violations are cleared, and lint runs on every PR. Types and UI tests do too. (#1101, #1161)

### Fixed
- **The docs page loaded every page's body to draw a tree.** Opening `/repos/[id]/docs` on this repo meant twelve sequential round trips and 38.6 MB across 5,485 pages, none of whose text the tree renders. The four things that read bodies off that list are each served directly now. (#1120)
- **The architecture graph spent seconds arriving and kept paying per frame.** The minimap redrew every node on `afterRender`; `getComputedStyle` ran 3,022 times on the mount colour pass and again on every theme flip; ELK wrote 3,000 graphology events for one position update. Measured on this repo's capped export (1,500 nodes, 4,107 edges), those are 0, 36, 12 and 1. Dimming also blended toward the wrong colour. (#1149)
- **The graph stopped blaming the backend mid-load.** An error state rendered while data was still arriving. (#1146)
- **`get_health` dropped targets it could not resolve** and repeated itself, so an agent could not tell "not indexed" from "no findings". Nothing about scoring changed; this is the MCP response surface. (#1142)
- **`get_answer` synthesis was capped at a flat 30 seconds,** a budget sized for a remote API. Providers that shell out to a coding-agent CLI or drive a local model need 40 to 120 seconds for one turn, and those are exactly the providers `init` offers users with no API key. The budget is per provider now. (#1119, #1124)
- **`get_answer` reports an empty completion** instead of shipping a blank answer as if it were one. (#1126)
- **A `--resume` run deleted pages it had just protected.** Pages skipped because they already existed were absent from the produced set, which is what the stale sweep treats as deleted. (#1089, #1097)
- **A page whose provider call failed is kept** rather than dropped from the wiki. (#1104)
- **The augment hook is silent when its console script is missing** instead of printing an error into every agent turn. (#1141)
- **`repowise --help` no longer imports the generation pipeline.** One module-scope import in the generate engine pulled the orchestrator and persist layer into every invocation, including post-commit hooks. (#1140)
- **Malformed `.gitignore` patterns are tolerated** rather than failing ingestion. (#1095)
- **Command palette answers a single keypress.** It used to need two. (#1157)
- **Nav links land where their label says.** (#1158)
- **The docs reader** shows one breadcrumb, computes its height correctly, and wraps long backlinks. (#1156)
- **Dead code:** the default `min_confidence` floor aligns with `RISK_CAP_CONFIDENCE`, and the zombie-package detector reads git metadata with `dict.get()` instead of assuming the key. (#1084, #1087, #1128)
- **Coupling keeps `?focus=` in sync with the pinned file.** (#1088)
- **Pages parented to the repo root use the stored concept tree.** (#1081, #1085)
- **Server jobs fail explicitly on an unknown execution mode** rather than silently. (#881, #911)

### Documentation
- **Contributors are onboarded through repowise itself,** and the README badge row is trimmed. (#1110)
- **Website pages resynced with the shipped surface:** the MCP pages match the live default tool set, the language list matches the 16 AST languages, and the obsolete Anthropic Message Batches / `--no-batch` material is gone. (#1122, #1135, #1136)
- **New walkthroughs** for `distill` / `expand` / saved output, the security scan, health coverage, and OpenCode provider setup. (#1020, #1021, #1109, #1123)
- **The commercial page acknowledges the OSS full-history secret scan.** (#1105)
- **Plugin:** Claude Code and Codex skills and commands updated for the `--no-editor-setup` flag, the dead-code confidence floor, the `get_health` response shape and the augment hook change.

### Internal
- **One home for "is this path a test?"** The last inline test-path checks in core and the server are converted to the shared rules, and `.mts` / `.cts` tests are classified correctly in `coverage_gradient` and `move_method`. (#1103, #1111, #1112, #1113, #1115)
- **CLI test coverage** for `repowise decision list/show/confirm/dismiss/health` and the `security scan` stub. (#1108, #1138)

---

## [0.36.0] — 2026-07-25

A smaller release that settles what 0.35.0 started. Wikis on very large repositories now bound their own file-page volume by default instead of emitting one page per file, `distill` learns a real shell lexer and gains install and infra command families, and a run of contributor fixes lands across serve, the job scheduler and dead code.

### Added
- **Install and infra command families for `distill`.** `pip`, `uv`, `poetry`, `npm`, `pnpm`, `yarn`, `cargo` and `brew` output, plus `terraform`, `tofu` and `helm` plans, are now compacted by data-driven TOML filters. A new family is a data file rather than a Python module, and the errors-first invariant is enforced mechanically so no filter can drop an error line. (#1067)
- **A shared shell lexer behind the rewrite hook.** The hook used to scan for shell characters and could not tell a pipe from the same character inside a quoted string, so it refused anything with both a quote and punctuation. A single-pass tokenizer replaces that scan, which makes the bailouts structural and widens the safe final pipeline stage beyond `head` and `tail`. (#1070)

### Changed
- **File pages are rationed on very large repositories, by default.** A 10.8k-file monorepo produced 8,756 file pages inside a 14,027-page wiki, roughly 77 MB whose tail mostly restates the concept page above it. `max_file_pages` now scales with repo size using two thresholds derived from the size distribution of indexed repositories: an interactive run offers a leaner wiki past 2,000 files, and an automatic ceiling of 4,500 applies to runs that never get asked, such as `--yes`, agent and CI runs. Smaller repos are unaffected. (#1074, #1076)
- **Symbol spotlight pages take the top decile, not the top fifth.** That bucket restates what a symbol's file page already renders, and at 4,996 pages it buried the pages that say something new. The bucket still floors at one, so small repos keep a spotlight. (#1069)
- **A tidier docs nav tree.** Repeated folder glyphs are gone from concept rows where indentation already said it, top-level sections carry more weight, and a leading heading that just repeats the page title is stripped in the reader. (#1066)
- **The health files API reports a repository's real file total** rather than however many rows the current page happened to carry, while still accepting the older bare-array response. (#1080)

### Removed
- **`--safe-only` on `repowise health`**, which never did anything. The flag remains live and functional on `repowise dead-code`. (#1027)

### Fixed
- **Command injection when `distill` rejoined argv on Windows.** The rendered command was quoted for the C runtime's argv parser rather than for `cmd.exe`, so a token carrying a metacharacter and no space, such as `--grep=a&whoami`, came back unquoted and the shell read the `&` as a separator. Verified by execution before the fix: `&` and `|` both ran injected commands and `>` created a file. Every `"&|<>^()` is now caret-escaped. (#1071)
- **The getting-started page crashed on any repo with a dependency.** The context dict never carried the `version` key its template reads, and page rendering runs under `StrictUndefined`, so a missing key raised instead of being falsy. (#1068)
- **`repowise serve` reclaims its preferred ports on restart.** The pre-flight probe bound without `SO_REUSEADDR`, so a port still in `TIME_WAIT` from the just-exited instance read as busy and the API silently moved to 7338 while the UI kept proxying to 7337. The result was a healthy server rendering as empty panels. The probe now matches the bind semantics uvicorn and Next.js actually use, on POSIX only, since Windows treats the option differently. (#840, #845)
- **Persisted polling sync jobs never launched.** The fallback path discarded the job returned by `upsert_generation_job` and then referenced an undefined name, and the resulting error was swallowed by an outer handler, so the failure was silent. (#831)
- **Subsystem pages no longer print "Questions this page answers" twice.** The requirement was stated in both the system prompt and the template, and under the doubled instruction the model emitted the heading twice on 86 of 92 measured pages. (#1073)
- **Top-level `export const` in TypeScript and JavaScript is evaluated for dead code** instead of being exempted the way class members are. (#1065)
- **Alias resolution in `generate_refactoring_code`**, which passed a redundant argument inside an already alias-scoped session. (#884)

### Documentation
- **The docs lead with interactive `init`.** Getting-started paths handed new users `repowise init --index-only -y` and never mentioned that a bare `init` scans the repo and offers a choice before spending anything. Every path now says `--no-prose`, since `--index-only` has been a deprecated hidden alias since #1032. (#1075)
- **Live badges on the README.** We ask other projects to carry a Repowise badge and had none of our own. Both read live endpoints, so they follow each index rather than freezing at whatever was true when someone pasted them in. (#1079)

---

## [0.35.0] — 2026-07-24

This release rebuilds how wikis are generated. Every page now renders from structure with no key and no spend, except a single model-written subsystem layer; a new `repowise generate` command fills that layer on demand, and the web UI can generate AI docs page by page. Stores built before this change are recommended (never forced) to re-index for the newer navigable wiki.

### Added
- **`repowise generate`, scoped and cost-gated wiki generation.** A new command writes or refills the model-written subsystem pages on demand, gated behind a single cost question, and onboards a provider the same way `init` does when a docs run needs one. It is also exposed over HTTP for the web UI. (#978, #982, #983, #1060)
- **Keyless, deterministic wikis.** `init` (and `init --index-only` / `--no-prose`) produces a complete, honest wiki with no API key and no spend: file, symbol, API, infra, cycle and layer pages all render from structure. `init --docs` opts into the model-written subsystem prose, and a template wiki renders when no provider is configured. (#972, #975, #976, #999, #1001, #1003)
- **AI docs from the web reader.** The web UI gained provider-key settings and per-page and bulk AI documentation generation from the reader, with a coverage picker and provenance surfaces, streamed over an authenticated job channel. (#988, #991, #994, #984)
- **Full git-history secret scanning.** `repowise security scan --history` scans the entire git history for secrets, not just the working tree. (#821)
- **Hybrid `search_codebase`.** Full-text and vector results are now fused with reciprocal rank fusion, so search blends exact-term and semantic matches instead of picking one. (#1057)
- **Interactive change-coupling page**, surfaced on the Overview. (#986)
- **Fix history in the file UI.** The symbols view marks which symbols in a file keep getting fixed, and a file's governing decisions moved into their own tab. (#960, #966, #968, #977)
- **Dead-code page rebuilt** on a shared table primitive with one confidence scale, a reopen path, and per-finding AI prompts, links and reasons. (#990, #992, #993)
- **Disable reasoning on Ollama models** for providers that support it. (#1009)
- **Adaptive TSX grammar fallback** so `.ts` files that contain JSX still parse. (#967)

### Changed
- **One wiki, one renderer per page type.** The old template-vs-model split is gone. Every page except the subsystem layer is rendered from structure, always, with no key and no spend: file, symbol, API, infra, cycle and layer pages. Above them sits a derived, numbered concept tree (the `module_page` type) that is the one model-written layer: model prose when a provider is configured, structural stubs otherwise. `repowise generate` fills or refills that subsystem prose. (#1023, #1024, #1025, #1032)
- **A re-index is recommended after upgrading.** Stores built before the subsystem-page restructure carry per-directory pages and no navigable tree, which no automatic reconcile can rebuild. Such stores are now told (never forced) that a full re-index would give them the newer wiki; ordinary updates keep working and the notice surfaces at most once. (#1035)
- **`--prose` / `--no-prose` is the single wiki-spend switch.** `--prose` writes the subsystem pages as model prose (needs a key); `--no-prose` renders the whole wiki from structure with no model and no cost. `--index-only` and `--docs llm|deterministic` remain as deprecated hidden aliases. The interactive coverage chooser and the `--coverage` / `--top` ranked-generation flags are removed; `init` and `generate` now ask a single cost question. (#1032, #1036)
- **Hierarchy lives in the data model.** `wiki_pages` carries `parent_page_id`, `display_order`, `section_number` and a stable `structural_key`, so MCP (`get_overview`), the web docs tree and the breadcrumbs all read one stored tree instead of each deriving their own. (#1014, #1022)
- **Subsystem pages read like subsystem docs.** The concept tree's `module_page`s open by situating themselves against their siblings, weave in git-derived health and history signals, and close with a "Questions this page answers" section; a parent directory that spans several concept pages gets its own overview page. (#1043)
- **`get_answer` leads subsystem questions with their concept page.** A question about a subsystem as a whole now surfaces that subsystem's page above its individual file and child pages, instead of returning the parts without the whole. (#1046)
- **Retrieval demotes decision records and test pages**, and `get_context` reports where a file sits in the concept tree. (#1040, #1056)
- **Dead-code confidence default is aligned** across the CLI, REST and MCP surfaces, so the same threshold applies everywhere. (#1030)

### Removed
- **The template-vs-AI provenance axis.** `is_deterministic` and `doc_tier` are gone from the API responses and every TypeScript type; the Auto / AI / Mixed page badges, the "Auto-documented" partition, and the per-page "Write with AI" affordance are retired. The pages router's `?deterministic=` filter becomes `?has_prose=`, scoped to the model-written page types. (#1037)

### Fixed
- Idle files recover their time-decayed health on `update` instead of staying stuck. (#728, #1061)
- The embedder is resolved from the store rather than the environment, so upgrades no longer switch models under you. (#1007)
- OpenAI temperature is clamped to 1 for GPT-5-and-later reasoning models. (#989)
- Ollama timeout and connection errors are wrapped so they retry. (#829)
- Workspace repos resolve correctly in chat tool calls. (#971)
- Stale `.update.pending` markers are cleaned up, and deferred workspace docs runs are reported honestly. (#1004, #1000)
- The overview summary self-heals a missing indexed commit, and repository sort timestamps are normalized. (#1005, #1058)
- Tombstoned pages no longer count in the docs tree or appear in status and export listings, and table-of-contents keys stay unique across repeated headings. (#1049, #1050, #1053)
- Workspace file counts and top-language queries are scoped to file nodes. (#952)

### Documentation
- Plugin: added `coverage` and impacted-tests slash commands, and synced the MCP tool surface and hooks with reality. The Codex plugin was brought to parity (code-health skill and skill guidance). (#1029, #1017, #962, #1016)
- `repowise security` documented in the CLI reference, an examples index added, and the README leads with the numbers and restores the five-layer table. (#1026, #1019, #961)

### Dependencies
- Consolidated security bumps across the dependency tree; lockfile packages pruned by the refresh were restored. (#1054, #1059)

---

## [0.34.0] — 2026-07-20

### Added
- **Bug-fix history is now a first-class signal.** repowise counts the bug fixes that landed on each file over the trailing six months, traces each fix back to the commit that introduced it, and attributes it down to the symbol. A file that keeps getting fixed is flagged a *bug magnet*, and that flag now leads everywhere risk is shown: `get_risk` and `get_change_risk` return a `defect_profile` (`fix_count`, `last_fix_days_ago`, `bug_magnet`, `top_symbols`), the generated `CLAUDE.md` attention list ranks on fix history instead of raw churn, `get_context`'s triage does the same, and the CLI warns at edit time when you touch a file with a recent run of fixes. Only fixes that change production code are counted, so a test-only touch-up no longer inflates the number. (#931, #939, #940, #946, #947, #954, #956)
- **Bug-fix history in the UI.** The health drawer, panel and hover show per-file fix history; the symbols view shows and filters by per-symbol fix counts; and the commits view replaces the old file-risk bars with commit-level distribution views. (#948, #949, #950)
- **A zoomable Knowledge Graph.** The zoom map is now the Knowledge Graph at `/knowledge-graph`, with per-node code health rendered on the map. (#918)
- **Present mode for docs.** Wiki pages can be presented as a slide deck with a guided walkthrough, and architecture diagrams in wiki pages are now deterministic rather than LLM-drawn. (#914, #915)
- **Costs page tells the whole story.** Spend is labelled by operation, local runs are recorded at $0 instead of being dropped, ROI framing was added, and the agent savings the ledger was silently discarding are now counted. (#925, #927)
- **`--verbose/-v` across the CLI.** `init` and `update` are quiet by default and show per-phase internals plus debug logs under `--verbose`; the same flag was added to `health`, `watch`, `restyle`, `generate-claude-md`, `coverage add`, and `workspace add` / `workspace scan`. (#929, #936, #937, #941, #942, #943, #944)

### Changed
- **`get_answer` always synthesizes.** Confidence is graded after synthesis on how well the answer is grounded in retrieved content, rather than inferred beforehand from the shape of the retrieval. A leaner high-confidence payload is available behind `REPOWISE_ANSWER_LEAN_HIGH`. (#919, #923, #938)
- **Update keeps more of the index fresh.** A workspace update regenerates docs per repo, onboards a provider when a docs update needs one, and refreshes external systems (C4 L1) when the manifest changes. (#916, #917, #921)
- **Faster generation.** Knowledge-graph enrichment overlaps page generation, and the inline-marker scan for decisions reuses ingestion's source map instead of re-reading files. (#912, #913)
- **The SZZ blame pass is gone** from the git indexer; fix attribution is derived without it. (#951)
- **Feedback CTA sharpened** and the recalibration banner dropped from the web UI. (#920)

### Fixed
- **Author experience is counted over the whole history**, not just the current update batch, so contributor stats no longer collapse on incremental runs. (#953)
- **Update progress counts onboarding pages** in the generation total, so the bar no longer overshoots. (#928)
- **Headline stats stop assuming a Sat/Sun weekend** and are given room to breathe. (#926)

### Documentation
- README rewritten and the `docs/` tree restructured into `start/`, `reference/`, and `layers/`; the quickstart was rewritten and the user guide collapsed to a guide. Provider extras that don't exist are no longer documented. (#957)
- Plugin: version bump plus `--verbose` on the `init`, `update`, and `health` commands, and the pre-modification skill now reads `defect_profile`.

### Dependencies
- `mcp` 1.26.0 → 1.28.1. (#958)

## [0.33.0] — 2026-07-18

### Added
- **Line-level AI authorship from your agent sessions.** repowise reads agent-trace records to attribute commits (and now individual lines) to the agent and model that wrote them, and stamps a per-commit model id, so the contributors and provenance views can tell human-written code from agent-written code down to the line. (#861, #866)
- **Docs that repair themselves.** Generation now self-repairs hallucinated symbol references, resolves cross-page links across the whole repo on updates, and backfills graph-derived "related pages" with a self-healing pass, so wiki pages link to real symbols and stay cross-linked as the code moves. (#871, #872)
- **`get_change_risk` grew up.** The change-risk score gained a `-x/--exclude` flag with `.riskignore` support to drop vendored or generated paths from the diff, and now lists the line-level tests a change actually impacts. (#867, #903)
- **Coverage-backed tests in PR risk.** `get_risk` PR mode now surfaces `tests_to_run` proven by the per-test coverage map, so a reviewer sees exactly which tests exercise the changed files. (#859)
- **Fewer dead-code false positives.** Whole classes of systematic false positives (re-exports, dynamic references) are eliminated, so `get_dead_code` reports far less that is actually live. (#886)
- **Better flow answers.** `get_answer` rescues un-named flow endpoints by re-ranking on the graph neighborhood, so "how does X get to Y" answers land on real endpoints more often. (#864)
- **Leaner MCP entry point.** `get_why` joins the lean MCP profile and `get_answer` is the advertised entry point; `get_conformance` and `generate_refactoring_code` are now opt-in. `AGENTS.md` was brought to parity with the `CLAUDE.md` tool surface. (#889, #875, #900)

### Changed
- **Faster init and update.** A broad performance pass across indexing: analysis phases run concurrently and incremental re-ingest runs in parallel, init- and update-built graphs converge so the centrality cache hits on updates, self-call resolution and pagerank are cached for cascade ordering, pages reused verbatim skip re-embedding, end-of-run wiki-page upserts are batched, refactoring detectors drop repo-sized per-file work, and coverage discovery plus the repo pre-scan share the pruned file walk. (#894, #895, #891, #892, #904, #893, #898, #897)
- **Cleaner UI.** The Overview got a visual-hierarchy pass and a denser health card with docs counts split by AI vs auto-generated, and "Attention Needed" is a triage panel again. (#882, #879, #883)
- **TypeScript path resolution in the CLI.** `TsconfigResolver` is now wired into the CLI commands, so `tsconfig` path aliases resolve during indexing. (#675)

### Fixed
- **Docs pointer no longer drifts on index-only updates.** The docs commit pointer is preserved during index-only updates and kept separate from the sync pointer, fixing `update --docs` drift. (#849, #878)
- **Wildcard re-exports are followed in call resolution.** `from x import *` re-exports (and the JS equivalent) are now traced, recovering call edges that were dropped. (#905)
- **`get_answer` stops dropping substance.** Synthesis is no longer silently skipped when a usable API key exists, the gated low-confidence path serves real substance and unpins cached misses, gated-path excerpts no longer die to a swallowed error, and the live-grep fallback respects gitignore and exclude patterns. (#888, #887, #896, #856)
- **Sturdier change-risk on the CLI.** A friendly revspec error is restored in `changed_lines`, and subprocess handling is hardened and aligned with the MCP tool conventions. (#902, #899)
- **Security scan reads real source** and guards repo-root detection, instead of scanning stale or wrong content. (#890)
- **Smaller fixes.** Unknown `restyle` style arguments raise a usage error; CLI usage errors are classified separately from failures in telemetry; decision staleness timestamps are normalized; webhook jobs run in sync mode; MCP health resolves the repository from the scoped session; and the code-rationale git-grep is hardened for macOS and color configs. (#908, #907, #877, #848, #865, #825)

### Documentation
- README now counts ten MCP tools, fixes the coverage example, and tightens the Code Health section. (#901)
- Plugin: both the Claude Code and Codex plugins ship a `change-review` skill; the risk command documents the new `--exclude` flag.

## [0.32.0] — 2026-07-15

### Added
- **Every file is now retrievable.** After the LLM budget picks its file pages, every remaining parsed code file gets a zero-LLM deterministic page, so concept search reaches the whole codebase instead of only the ~20% the budget covers. These tail pages are down-weighted in `search_codebase` and `get_answer` so a thin template page never displaces a rich page on a tie, and they carry `is_deterministic` / `doc_tier` in the API responses. (#817, #819)
- **More signals on the stats page.** "By the Numbers" gained a coding-rhythm punch card (weekday × hour commit heatmap, UTC), 90-day momentum against the prior window, the wiki's own build cost stats, a change-risk mix, and a truck factor — all derived from data the index already holds, with no new indexing. (#828)
- **Kimi is a first-class provider.** (#824)
- **Decisions follow you into Codex.** The Codex SessionStart hook now delivers relevance-ranked standing decisions, matching what Claude Code already did. (#788)
- **Test breakage is called out separately in risk.** `get_risk` PR mode splits test files into a new `will_break_tests` field, so a burst of broken tests can't crowd real structural impact out of the capped `will_break` list. (#739)
- **Richer anonymous telemetry.** One event per MCP tool call, an interrupted status, and index-shape outcomes, so the collected numbers answer real questions. Consent and env vars are unchanged. (#820)

### Fixed
- **Search scores the identifier, not the whole question.** (#853)
- **A generic method mention no longer hijacks the answer.** A prose `get_answer` question that merely mentioned a common method (`to_dict`, `provider_name`) was routed into the exact-name union path and returned the whole definition set instead of answering. Large unions on prose questions now defer to synthesis; small unions and bare symbol lookups are untouched. (#823)
- **Key Concepts picks real concepts.** Ranking was by file PageRank, so every public symbol in a high-ranked file inherited that rank — surfacing five methods of one registry class as the codebase's core concepts, and leaking pytest fixtures into the prose. Symbols are now ranked directly and spread across clusters. (#813)
- **Custom `serve` ports work again.** Next.js standalone compiled rewrites with build-time env vars, breaking custom port arguments; `/api/*`, `/health`, and `/metrics` now proxy through runtime middleware. (#838, #839)
- **Persisted vector indexes load on startup.** (#835)
- **Go dead-code false positives.** Function references in Go are now detected, rescuing live functions that were reported as dead. (#815)
- **Dataflow recurses into `with`-statement bodies.** (#836)
- **Stats report true project age, commit, and contributor counts**, with compact k/m formatting and years/months age in the hero. (#730, #827, #798)
- **Source citations show normalized confidence**, not the raw score. (#846)
- **Anthropic thinking blocks are handled.** (#787)
- **Add Repository dialog no longer overflows its inputs.** (#843, #844)
- **Friendlier empty state for documents.** (#822)

## [0.31.0] — 2026-07-12

### Added
- **Coverage-backed test intelligence.** repowise now ingests per-test coverage to build a test-to-code map, and puts it to work two ways: a missing-test signal proven by that map (real gaps, not just heuristics), and a new `repowise impacted-tests` command that takes a diff and lists only the tests that actually exercise the changed code, so you can run the smallest relevant subset instead of the whole suite. (#789, #790, #792, #793)
- **Decisions mined from your agent sessions.** A shared agent-transcript layer lets repowise mine durable architectural decisions out of Claude Code / agent session transcripts, then deliver them relevance-ranked at session start and at edit time with a usage-feedback loop. Agent co-authorship is detected and decision scope is derived from it. (#774, #775, #776, #803)
- **Flow-shaped answers.** `get_answer` can now trace how something gets from one named endpoint to another by walking the graph path between them and narrating the flow. (#804)
- **Shell parsed to a real AST.** Shell scripts now parse into functions, source edges, and complexity like every other supported language, instead of being treated as opaque text. (#768)
- **Lean MCP-only Docker image.** A new `Dockerfile.mcp` builds a slim MCP-server-only image for MCP hosts and server directories. (#800)

### Changed
- **`get_answer` is grounded in what it serves.** Synthesis is grounded in the exact body the answer returns, data-shape questions are grounded in the field set mined from source (including alias keys the shape omits), homonym symbols are answered by union across their definitions, and low-confidence misses are slimmed. `get_symbol(id=...)` is forgiven and a hedged answer is re-grounded on the served body. (#778, #779, #780, #772, #783)
- **Performance findings move the health score.** Open performance-risk findings are now deducted from the performance score, so the number reflects outstanding perf debt. (#801)
- **Decision sources gated and stickier.** The code-comment decision harvest is gone, decision sources are gated, and dismissals stick so a dismissed suggestion stays dismissed. (#773)
- **FAQ-weighted docs budget.** Generation tilts documentation depth toward the modules people actually ask about. (#781)
- **Hardier MCP surface.** Session-survival hardening for the tool surface, plus a response budget clamped under the live MCP host token cap, keeps the server responsive under strict hosts. (#767, #771)
- **Prose search leads with concept pages.** Hybrid search now leads prose queries with concept and wiki pages instead of raw symbol hits. (#797)
- **Contributors strip explains agent authorship.** The contributors view now spells out the agent-authored share of commits. (#799)

### Fixed
- **Incremental updates persist graph and symbol data.** `repowise update` now persists `graph_edges` and `wiki_symbols` on incremental runs (both were dropped before), and edge upserts keep the maximum confidence instead of overwriting it. (#795, #806, #807)
- **`get_answer` verifies live slices.** Symbol bounds are verified before serving a live slice, and non-path node ids are kept out of `fallback_targets`. (#796, #769)
- **Honest search and symbol signals.** Zero-exact search and filename-only `get_symbol` carry honesty signals instead of implying a confident hit. (#770)
- **Execution-flow picker on the module overview.** The module overview gained an execution-flow picker with hierarchical-layout guardrails so large graphs stay readable. (#805)

### Documentation
- New hooks guide plus a "learns from your usage" section explaining the adoption feedback loop. (#784)
- Full AGPL-3.0 license text so GitHub detects the license correctly. (#794)

---

## [0.30.0] — 2026-07-10

### Added
- **First-class output-language support.** `repowise init --language <code>` (15 languages: en, ru, es, fr, de, zh, ja, ko, it, pt, nl, pl, tr, ar, hi) sets the natural language for generated wiki pages; advanced interactive mode now asks for it too (English default). The choice persists to `.repowise/config.yaml` so `update` regenerates changed pages in the same language, and the workspace init, workspace generate, and server regenerate paths now honor it instead of silently defaulting to English. Code, file paths, and symbol names stay untranslated. Previously this was config-file-only (#99). (#756)
- **Worktrees just work.** `repowise init` and `repowise update` inside a linked git worktree now auto-detect the base checkout and seed the worktree's index from it, then catch up incrementally; no flags needed. `--seed-from <base>` remains as an explicit override and `--no-seed` forces a cold init. See [WORKTREES.md](scale/WORKTREES.md). (#655 introduced the manual flag; #747 makes it automatic.)
- **Ruby and Scala promoted to Full health tier.** Both languages now get the full complexity node map and performance-risk dialect, so health scores and perf findings match the depth Python/TypeScript/Go already had. (#745, #749)
- **Git stats awards.** The contributor stats view gained biggest-commit, longest-streak, most-imported-file, and dependency awards. (#752)
- **Workspace socket-contract detection.** Cross-repo analysis now detects socket-based service contracts alongside HTTP ones. (#710)
- **Anonymous feedback panel.** The dashboard gained a lightweight feedback panel — no account or email required. (#763)
- **Claude Code integration: session context + safer distill.** The augment hook now emits a per-session context block with live index freshness on session start, the distill rewrite hook learned two safe shell-syntax carve-outs, and `repowise doctor` checks hook registration. (#743)

### Changed
- **Unchanged wiki pages are reused across runs.** Cross-run page reuse now keys on the documented file's content plus the generation settings (template, language, style) instead of the exact rendered prompt — which embedded per-run retrieval context and practically never matched. A re-run over a repo where a handful of files changed now regenerates only those pages; changing the model, output language, or wiki style still regenerates everything it should. (#757)
- **One dataflow parse per file.** The health pass's dataflow consumers (the Extract Method detector and the perf advisory-to-asserted promotion) now share a single lazily parsed per-file analysis instead of each re-reading and re-parsing the file, making the health pass measurably faster on large repos with identical output. The new per-function dataflow summary and point-lookup APIs this introduces are the foundation for upcoming dataflow-backed surfaces. (#755)
- **Leaner MCP surface for agents.** An agent-lean tool profile with tool-search gating and registry-pinned docs, plus payload-precision trims across tools, cut the token cost of the MCP surface without removing capability. (#742, #744)
- **Graph view cleanups.** The module view no longer collapses into a single blob, external nodes are hidden by default, and node info panels are layer-aware. (#754)

### Fixed
- **Pages skipped by the update budget are labelled stale.** `repowise update` marks weakly-affected pages it chose not to regenerate as stale instead of leaving them claiming to be fresh, and `total_pages` is recomputed from the store. (#748)
- **Dataflow CFG no longer flags reachable code as unreachable.** (#760)
- **TypeScript object-literal shorthand properties count as reads**, fixing spurious unused-symbol findings. (#759)
- **Cross-repo co-change is a bounded session-share signal**, preventing one shared session from dominating workspace coupling scores. (#758)
- **Seeded worktree indexes no longer split in two.** Seeding now re-points the copied index at the worktree, so the first update reuses the seeded pages instead of minting a second repository entry and regenerating everything under it. (#747)
- **`[workspace]` notices render again.** The one-line workspace auto-detect notice was being swallowed by console markup and printed without its prefix. (#747)

### Documentation
- User-facing docs restructured and refreshed (quickstart, user guide, CLI reference, config). (#741)
- Plugin: the Claude Code plugin bundles the new SessionStart context hook. (#743)

---

## [0.29.0] — 2026-07-09

### Added
- **Leverage-weighted health signals.** `get_health` now surfaces NLOC-weighting and per-file leverage, so the score points at where a fix buys the most. (#719)
- **Wider agent-authorship detection.** Git indexing recognizes agent-written commits across more provenance channels. (#731)

### Changed
- **Leaner `get_overview` by default.** The `get_overview` MCP payload is compact by default, cutting the token cost of orienting an agent. (#729)
- **Health scores reflect corrected findings.** Removing the false findings below raises some file and repo health scores — this is expected and desirable, not a regression. Broad `except Exception` catches are now detected completely (they were previously under-counted), so a few files surface additional — but honest — findings.

### Fixed
- **Honest exception-handling rationale.** A broad `except Exception` is no longer described as catching `KeyboardInterrupt`/`SystemExit` — only a genuine bare `except:` or `except BaseException:` carries that warning, and `except Exception` gets its own rationale. Go blank-identifier discards are only flagged when the discarded value sits in the error position, and Rust panic macros and `.unwrap()`/`.expect()` inside test code are no longer flagged as recoverable-error crashes. (#733)
- **Accurate complexity counting.** `elif` / `else if` chains no longer read as deep nesting (a flat guard chain is flat); parameter counts ignore the bare `*` / `/` separators; comprehension filters now count toward complexity; and docstring- and comment-only lines no longer inflate a function's or class's measured length. (#734)
- **Fewer false performance findings.** I/O-in-loop, defer-in-loop, blocking-I/O-under-lock and related markers no longer fire on a closure that is merely defined — not run — inside a loop or lock; a parenthesized `await` is recognized as awaited; `deque.insert(0, …)` is no longer flagged as quadratic; name-shadowing collisions in the Python and TypeScript detectors are resolved; and a semaphore-bounded goroutine worker pool is no longer called unbounded. (#735)
- **Truthful structural findings.** A god-class finding cites the complexity of the actual brain method rather than the class-wide maximum; an `UPDATE`/`DELETE` bounded by a `LIMIT` is no longer said to touch every row; a flat `match`/`case` dispatch table is no longer flagged as a large method; and hidden-coupling severity is no longer overstated from a handful of shared commits. (#736)
- **Cleaner, more private MCP output.** Contributor email addresses are never shown in overview output (display names only); extreme churn renders as a multiplier instead of a runaway percentage; search snippets no longer repeat a title-only decision; comment-derived decisions rank below real architecture decisions; file counts are labeled; and decision titles truncate on a word boundary with an ellipsis. (#737)
- **Atomic update lock.** `repowise update`'s lock file is now written atomically with its contents, closing a creation race. (#720)

---

## [0.28.1] — 2026-07-08

### Changed
- **Performance map colors by findings.** The performance code map now colors by open findings and detector coverage instead of the bounded score, so hot spots read at a glance. (#716)

### Fixed
- **Graph overlays show their nodes.** The full dependency graph reserves part of its node budget for dead-code files, hotspots, and execution-flow members instead of selecting purely by PageRank — the Dead/Hot overlays and flow highlighting no longer come up empty on large repos. The view says how many flagged files are in view ("12 of 37 dead files"), and empty overlays explain whether the repo has none or they fell outside the loaded set. (#714)
- **Graph controls explain themselves.** The hierarchical layout says why it won't run above 500 nodes instead of silently doing nothing; the Execution Flows panel gained a close button and Escape handling, and warns when a selected flow has out-of-view nodes; the Dead/Hot toggle pair became an exclusive All / Hot / Dead control. (#714)
- **Honest health drawer and stats labels.** Missing structural metrics render "not measured" instead of 0; score-breakdown bars scale against real category caps with a tooltip explaining the cap; the lines-of-code and agent-authorship stats now say exactly what they measure. (#715)
- **Decisions page polish.** Missing decision dates render a dash instead of "Invalid Date"; the decision graph bounds its layout so large decision sets can't hang the page; Confirm/Dismiss/Deprecate explain what they do; and a new "Enforce this decision" button generates a paste-ready agent prompt that audits governed code for compliance. (#715)
- **Honest performance coverage.** Dead performance markers are wired up and performance coverage is reported honestly. (#711)

---

## [0.28.0] — 2026-07-07

### Added
- **Lean 4 support.** A lightweight regex tier brings symbol extraction to Lean 4 codebases. (#600)

### Changed
- **Reliable incremental updates.** `repowise update` was reworked this cycle. Incremental runs now rebuild the knowledge graph, so an updated index stays as fresh as a full one instead of serving a stale graph (#702). The store is persisted and locked atomically with honest failure reporting, so an interrupted update rolls back cleanly rather than leaving a torn store behind (#706). The workspace and single-repo update paths were reconciled onto one code path (#703).

### Fixed
- **Cleaner code-health flagging.** Resolved false-flag presentation in Code Health across grouping, dominant-cause attribution, and floor magnitude. (#700)
- **Contributors counted once.** GitHub noreply emails are folded together, so one person no longer shows up as two contributors. (#701)
- **Fewer Go dead-code false positives.** Same-file type references are rescued from unused-export false positives in Go. (#629)

### Documentation
- **No-key quickstart.** Published a verified no-API-key quickstart in the README and on repowise.dev. (#627)

---

## [0.27.0] — 2026-07-05

### Added
- **VS Code extension 0.3.0.** The editor experience grew up this cycle. Refactoring plans can now be handed straight to an AI agent, and the extension exposes native chat tools so agents can query Repowise from inside the editor (#694). The SCM view gained change intelligence, per-file change risk, and symbol hover detail, alongside more reliable server discovery (#664, #691). The listing now leads with a hero walkthrough GIF and screenshots, and the extension icon reads correctly on dark themes (#696, #697).
- **Dart support.** A Dart AST tier brings symbol extraction plus health and performance markers to Dart codebases. (#689)
- **SQL and dbt intelligence.** Indexing now extracts SQL DDL symbols and dbt lineage (#683), models app-to-database contracts, and surfaces SQL-specific health markers (#687).
- **Java and Rust dataflow.** The Extract Method dataflow layer now understands def/use chains in Java and Rust, extending refactoring analysis to those languages. (#686)
- **`repowise login`, `logout`, and `whoami`.** New CLI commands to connect the CLI to your Repowise account. (#690)
- **Storage footprint in `status`.** `repowise status` now reports the on-disk size of the index. (#681)
- **Add-repo wizard.** The web app gained a guided add-repo flow with a cost preflight and a live first-index experience, plus first-run polish across the app icon, explore cards, and a collapsible workspace nav (#692, #685).
- **Accurate coverage tab.** The Coverage tab now paginates, sorts, and reports coverage accurately on large repos. (#665)

### Changed
- **One consistent UI.** A design pass unified how the dashboard renders tables, stat tiles, row banding, loading skeletons, error states, and tooltips, so every view behaves the same way. (#695)
- **Dataflow-verified performance findings.** Advisory performance findings are now verified against the dataflow layer, cutting false positives, with refactoring config wired through. (#684)

### Fixed
- **Health sync honors repo excludes.** Workspace health sync now respects the repo's configured excludes. (#638)
- **External dependencies no longer masquerade as files.** The Files view stops linking external dependency nodes as if they were source files. (#673)
- **More robust parallelism.** Parse and betweenness process pools now use `spawn`, avoiding fork-related instability on some platforms. (#679)

### Dependencies
- Added `sqlglot` (SQL parsing) and `tree-sitter-dart` (Dart grammar).

---

## [0.26.0] — 2026-07-03

### Added
- **VS Code extension.** Repowise now runs inside your editor. The extension manages the local server lifecycle, walks you through install to first insight, and registers the Repowise MCP tools for agent mode (#643, #644). Low-health files surface as diagnostics with gutter heat, and editor-native signals include live range risk scoring, a refactoring lens, dead-code line spans, and inline docs (#642, #644). A sidebar Home dashboard shows index freshness, a theme switcher, and consolidated trees (#650), and the shared visualization panels (graph, C4, health, blast radius) render directly in webviews (#649, #653). A settings panel configures editor signals and the server connection (#654), and the latest pass adds panel navigation and quieter defaults for an editor-native feel (#660). Install it from the VS Code Marketplace or Open VSX.
- **Continuous-zoom architecture view.** The server builds a zoom-map artifact that drives a smooth, execution-aware zoom across the architecture graph. (#626)
- **Configurable Ollama embedding timeout.** The Ollama embedding request timeout can now be set via environment variable for slower local models. (#656)

### Changed
- **Sharper `get_answer` grounding.** The `get_answer` MCP tool gained a frame-grounding gate and anchors rationale to in-code comments, with retrieval tuning across `get_answer` and `get_context`. (#621, #622)
- **Faster decision embeddings.** Decision embeddings are batched during persistence and reindex, cutting indexing work on decision-heavy repos. (#641)

### Fixed
- **Config languages no longer inflate language usage.** Configuration-file languages are hidden from the language-usage breakdown. (#623)
- **Index freshness stamp stays current on no-op syncs.** An `update` that finds no changes still refreshes the freshness stamp, so agents don't distrust a current index. (#652)

### Dependencies
- Cleared high and critical CVEs across the npm and Python dependency trees. (#645)

---

## [0.25.0] — 2026-06-27

### Added
- **Split File refactoring.** Code Health now detects files that should be decomposed into smaller modules and proposes a concrete split. A new detector identifies low-cohesion modules and groups their members into coherent target files (#607), with richer cohesion signals driving the grouping (#614). Each plan is browsable in the web Refactoring tab and can be turned into real code via the deterministic code-gen path (#608).
- **Extract Method refactoring.** Long, complex functions get an Extract Method suggestion computed over a real dataflow layer: an intra-procedural control-flow graph for flagged functions (#612), def/use chains and reaching definitions over that CFG (#613), and the Extract Method planner built on top (#615). The refactoring is available for Python, Go, and TypeScript/JavaScript (#616).
- **Coverage report ingestion.** Indexing can now ingest test-coverage reports, folding coverage into the code-health picture during a run. (#604)

### Changed
- **"Biomarker" is now "marker" in the UI.** Code Health display copy renames the user-facing "biomarker" term to "marker" across the web app and plugin surfaces; internal identifiers are unchanged. (#619)

### Fixed
- **`.` works as a glob pattern on Python 3.14+.** Passing `.` as a path/glob no longer errors on newer Python. (#609)
- **Decision harvest skips title-only records.** The decision harvester no longer emits empty records that carry only a title. (#605)

### Documentation
- Strengthened the code-health validation story and fixed stale references across the docs. (#617)
- Tightened the code-health docs, named CodeScene explicitly, and moved deeper internals into the architecture doc. (#618)

---

## [0.24.1] — 2026-06-25

### Changed
- **Workspace tables and the dependency-structure matrix stay responsive on large repos.** The co-change, repo-pair, contract-links, and package-deps tables now use the windowed virtualized table, and the dependency-structure-matrix grid is capped to the top-60 services by connectivity so a large workspace can't render tens of thousands of cells; counts still reflect the full matrix. (#602)

---

## [0.24.0] — 2026-06-25

### Added
- **Refactoring intelligence: deterministic, graph-aware refactoring plans.** Code Health now derives concrete refactoring suggestions from the dependency graph and health biomarkers, with detectors for **Extract Class**, **Extract Helper**, **Move Method**, and **Break Cycle** (#586, #587, #588). Each suggestion is a ranked plan card carrying impact, effort, blast radius, and evidence, browsable in a new web Refactoring tab with file-first cards, a visual plan modal, and one-click agent export (#589, #590). Plans can optionally be turned into real code: opt-in LLM code generation produces a change from a deterministic plan, viewable in a side-by-side diff viewer (#592, #594).
- **Airy Code Health overview with a Findings workbench.** The Code Health page was redesigned around a calmer overview and a dedicated Findings workbench for triaging biomarkers. (#593)
- **Browsable Files page.** A new Files page lets you browse the repo's files directly, with a restyled table and dark-mode polish. (#591)
- **`init` Advanced options.** `repowise init` gained an Advanced section with a docs toggle and a configurable index-only mode, and raised the commit-history cap. (#599)

### Changed
- **Large tables are virtualized.** A shared windowing primitive virtualizes large tables across the dashboard, keeping big repos responsive. (#598)

### Fixed
- **Execution-flow entry-point scores survive updates.** Incremental updates no longer wipe entry-point scores on the execution-flow graph. (#585)

### Documentation
- README and docs now lead with code health as a measure-locate-fix loop and document refactoring intelligence. (#595)
- Plugin: version bump to 0.24.0.

---

## [0.23.0] — 2026-06-23

### Added
- **Repo-understanding overhaul: knowledge graph, C4, and guided tour.** The C4 model now derives real actors, true coupling, and accurate containers instead of approximations (#576). The guided tour ranks orientation entry points by execution-start order and surfaces churn hotspots (#574), and docs/tooling files are routed out of the layer catch-all with an invariant reviewer gate to keep the layering honest (#575). The knowledge-graph info panel is now collapsible (#579).
- **Enriched `get_health` for pre-PR self-check.** The `get_health` MCP tool returns a richer payload so an agent can read the same signals the merge gate judges a change on before opening a PR. (#571, #572)
- **`init --no-workspace` and a fully non-interactive `--yes`.** `repowise init` can skip workspace setup, and `--yes` is now fully non-interactive for scripted/CI use. (#573)
- **Rust performance dialect.** Performance-risk detection gained a Rust dialect for I/O-in-loop / N+1 shapes. (#581)
- **Configurable health rules.** `health-rules.json` now supports severity overrides and a small-team profile. (#569)
- **Better cross-repo contract matching.** HTTP contract extraction resolves router mount prefixes (#567), and consumer-side matching gained hygiene filtering and base-URL service resolution (#568).

### Fixed
- **`GraphBuilder` is picklable across a process boundary.** Fixes a failure when the graph builder is handed to a worker process. (#583)
- **Parse cache versioned by `ParsedFile` schema shape.** The parse cache now keys on the parsed-file schema rather than the package version, so unrelated releases keep the cache warm and a schema change invalidates it automatically. (#582)
- **Knowledge-graph panel guards `matchMedia`.** The KG panel mount effect no longer assumes `matchMedia` is present, fixing a crash in environments without it. (#580)

### Documentation
- Plugin: version bump to 0.23.0.

---

## [0.22.0] — 2026-06-22

### Added
- **Three-signal code health.** The single code-health score is now split into three co-equal signals: **defect risk** (the headline score), **maintainability** (smells that raise change-cost without predicting bugs), and **performance risk** (static I/O-in-loop / N+1 shapes). Maintainability and performance are surfaced as their own pillars across the dashboard and the `get_health` MCP tool rather than being blended into the defect headline. (#528, #531, #533, #544)
- **Performance-risk detection across languages.** A new performance detector finds I/O-in-loop and N+1 shapes, including cross-function cases resolved through call-graph reachability, with language-specific markers and dialects for Python, Java, Go, and C# (loop-level markers, `pandas_iterrows_in_loop`, centrality gating, and a reusable severity ranker). Detection runs through a `PerfDialect` plugin registry. (#530, #532, #536, #537, #538, #539, #541, #542)
- **Dependencies classified by I/O boundary.** External systems are now classified by the kind of I/O boundary they sit on, feeding the performance and architecture views. (#529)
- **Reindex-free upgrades.** The on-disk store now carries a format version separate from the package version, so upgrading repowise no longer forces a reindex. Upgrades show a release advisory and a "what's new" panel on the CLI, and the dashboard surfaces available upgrades with release info shared through core. (#553, #554, #556)
- **Hybrid symbol and path search in `search_codebase`.** `search_codebase` now searches repowise's own structural index for identifier- and path-shaped queries instead of only running wiki-semantic search. A new `mode` parameter (`auto`/`concept`/`symbol`/`path`/`hybrid`) controls routing; `auto` picks by query shape and returns symbol IDs, file/line bounds, and signatures for identifier queries. (#558)
- **Anonymous, opt-out CLI telemetry.** The CLI now collects anonymous usage telemetry behind a central platform layer; it is opt-out and collects no source code. (#555)
- **AI prompt actions across the dashboard.** Health findings now carry MCP-native "fix this" AI prompt actions, rolled across the dashboard, with a finding-count cap to keep output bounded. (#546, #547)
- **Commits and stats redesigns.** The commits page leads with a Code Evolution timeline, the blast-radius impact tab was redesigned, the owners view leads with a knowledge-distribution headline, and a new repo Stats "By the Numbers" page was added. Co-changes gained a repo-pair summary drill-down. (#543, #548, #549, #550, #551)

### Changed
- **Distill: seamless rewrite permissions + re-read savings.** `repowise distill` gained smoother rewrite-permission handling and additional re-read token savings. (#559)

### Fixed
- **Overview "Last synced" reflects CLI auto-syncs.** The overview page now reflects auto-syncs triggered by the CLI in its "Last synced" timestamp. (#564)
- **Dead-code analyzer keeps same-file Python symbols.** Python symbols referenced only within their own file (callable-as-argument, annotation-only) are no longer flagged as dead code. (#563)
- **Index freshness stays current.** Updates now keep the `CLAUDE.md` stamp and the indexed commit current so agents don't distrust a fresh index. (#524)
- **Commits-page follow-ups.** Full-width agent strip, collapsible risk panel, and repo-wide stat cards on the commits page. (#552)
- **Fewer performance false positives.** Eliminated three perf-detector false-positive classes across C#/Go/Python and now skips `for...of` over constant collections in the TS/JS detector. (#540, #545)

### Documentation
- README refreshed: banner, numbers-first lead, combined demo GIF, three-signal code-health framing, and star CTAs. (#534, #535, #562)
- Plugin: version bump to 0.22.0; `search_codebase` skill docs updated for hybrid symbol/path search.

---

## [0.21.0] — 2026-06-19

### Added
- **Cross-repo workspace intelligence.** Workspace mode gained a live system map of cross-repo services, backed by a service-granular system graph with extraction diagnostics, cross-repo blast radius and change risk, and a breaking-change guard that flags edits to contracts other repos depend on. (#511, #512, #513, #514)
- **Architecture analysis.** New architecture conformance checks, dependency-cycle detection, a design-structure-matrix (DSM) view, and architecture metrics (propagation cost, core/periphery roles, and a 1-10 architecture score). (#515, #517)
- **Repo-wide change-coupling graph.** A new graph surfaces files that tend to change together across the whole repo. (#497)
- **Wider cross-repo contract extraction.** HTTP contract extraction now spans more languages and frameworks: Rust HTTP route providers and reqwest consumers, C#/Unity consumers, and JS wrapper / variable-URL consumers. Extractors were split into per-framework dialects for maintainability. (#505, #506, #507, #508, #510)
- **Configurable MCP tool surface.** The set of tools the MCP server advertises is now configurable. Workspace-only tools (`get_blast_radius`, `get_conformance`, `get_architecture`) are advertised only in workspace mode instead of always, and two extra tools (`get_dependency_path`, `get_execution_flows`) can be opted in. Configure it with an `mcp.tools` block in `.repowise/config.yaml` (`+`/`-` deltas, an explicit allowlist, or `all`) or per launch with `repowise mcp --tools` / `--all`. (#520)
- **MCP tool surface editor in the dashboard.** The Settings page now lists every tool with its description and lets you toggle the surface per repo, writing the selection back to that repo's `mcp.tools` config. Backed by `GET`/`PATCH /api/mcp/tools`. (#521)

### Changed
- **Code-health-first repo overview.** The repo overview page was rebuilt around code health. (#501)
- **Airier, diagram-first web UI.** A UX overhaul restyles the dashboard on a shared composition backbone, with more whitespace and diagram-forward layouts. (#504)
- **Consolidated the MCP tool surface.** Removed six redundant MCP tools (`annotate_file`, `get_callers_callees`, `get_community`, `get_graph_metrics`, `get_architecture_diagram`, `update_decision_records`) whose capabilities are already covered by `get_context(include=[...])` and `get_why`. The MCP server exposes 13 tools: 10 in single-repo mode plus three workspace-only tools (`get_blast_radius`, `get_conformance`, `get_architecture`). Documentation and tool counts across the project were reconciled to match. (#519)

### Fixed
- **Contract extraction no longer scans nested repos.** Workspace contract extraction could hang scanning up to a million files when a repo contained nested checkouts; it now uses the shared file traverser and skips nested repos. (#516)
- **C# gRPC consumer extraction requires gRPC context** before treating a client call as a cross-repo consumer, removing false positives. (#509)
- **Skip Unity-generated dotnet scan paths during ingestion.** (#499)

### Documentation
- Plugin: version bump to 0.21.0; MCP tool surface docs reconciled to the consolidated, configurable set.

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
- **Code-health scoring recalibrated.** The organizational category cap is lifted from −1.0 to −3.5 so the strongest empirical predictors (`developer_congestion`, `untested_hotspot`, `hidden_coupling`) are no longer suppressed, and a per-biomarker weight multiplier was added (`developer_congestion` ×1.5, `untested_hotspot` ×1.3, `function_hotspot` ×1.2). `knowledge_loss` is de-rated to ×0.4 per OSS calibration (legacy code that works gets handed off) — enterprise users can raise it back via per-repo overrides. See the updated category-cap table in `docs/layers/CODE_HEALTH.md` (#221).
- **Health web UI surfaces the new biomarkers.** Glossary entries and biomarker-specific detail views for all four new biomarkers (partner-file chip for `hidden_coupling`, operator count for `complex_conditional`, mod/p80 ratio for `function_hotspot`, median age + recent edits for `code_age_volatility`), recalibrated category caps in the score breakdown (now sorted by applied deduction), and a clickable `function:line` deep-link in the file drawer. The new biomarker details also flow into the AI refactor prompt (#223).
- **Doc generation scales to very large repos.** Batch embedding (one model call per generation level instead of N upserts), pipeline checkpoint/resume over the new JobStore seam, and graph metrics computed in SQL. Default behavior is unchanged when the new knobs aren't set (#220).

### Fixed
- **`repowise update --full` now recomputes code health at the FULL tier.** The upgrade backfilled the git tier and regenerated docs but never re-ran the health analysis, so the persisted health tables stayed frozen at the fast index's ESSENTIAL-tier findings — the blame/co-change biomarkers stayed invisible after an upgrade. The upgrade now runs a full-repo health pass against the rehydrated graph and persists findings/metrics/snapshot, matching what `init` and the normal `update` path do (#225).

### Documentation
- README mentions the Repowise PR Bot in the hosted-version section (#217).

---

## [0.10.0] — 2026-05-18

### Added
- **Code health layer — a fifth intelligence layer alongside graph / git / docs / decisions.** New `repowise health` command, `health_*` SQLite tables, biomarker engine, and `/repos/[id]/health` web UI surface what hotspots actually *cost* to maintain. Tree-sitter complexity walker feeds 10+ biomarkers across structural (`large_method`, `nested_complexity`, `bumpy_road`, `complex_method`, `brain_method`, `primitive_obsession`), organizational (`developer_congestion`, `knowledge_loss`), test (`untested_hotspot`, `coverage_gap`), and DRY (`dry_violation`, backed by a tokenizer + Rabin-Karp clone detector with co-change correlation) categories. A composite 0–10 score is rolled up per file, per module (NLOC-weighted), and per repo. Findings persist with deterministic refactoring suggestions and an `acknowledged | resolved | false_positive` lifecycle. `HealthSnapshot` writer + trend detector surface declining-health alerts. `.repowise/health-rules.json` supports per-file overrides. `repowise update` runs an incremental upsert so the dashboard stays fresh without re-running the full pipeline. New `repowise health --trend`, `--refactoring-targets`, `--module`, `--file`, and `--coverage` flags; `repowise status` prints a one-line health digest. Parallel biomarker analysis via `asyncio.gather`. Full architecture deep-dive in `docs/layers/CODE_HEALTH.md` (#212).
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
- **Workspace mode is now first-class across the CLI.** Every relevant command auto-detects whether it's running inside a workspace root and routes accordingly, with a one-line `[workspace] …` notice when it does. New flags `--no-workspace` (force single-repo) and `--repo <alias>` (scope to one repo) on `update`, `status`, `watch`, `doctor`, `costs`, `search`, `dead-code`, `decision`, `generate-claude-md`, `hook install/status/uninstall`. `costs` and `search` also gained `--all` for explicit workspace-wide fan-out. New `Workspace auto-detect` section in [CLI Reference](reference/CLI_REFERENCE.md) (#173).
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
- **Computed glossary** — `docs/reference/COMPUTED_GLOSSARY.md` documents every derived metric, score, and signal Repowise computes (PageRank, hotspot score, freshness, confidence tiers, etc.) so the surface vocabulary is discoverable in one place (#127).
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
- **Language tier promotion** — C# moves from "Good" to "Full" in `README.md` and `docs/layers/LANGUAGE_SUPPORT.md`. Eight languages now sit at Full tier (was: seven).
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
