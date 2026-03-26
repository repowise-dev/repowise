# MCP Tools Test Report

**Date:** 2026-03-26
**Tested on:** repowise repo (self-hosted, 315 files, 52k+ LOC)
**Server transport:** stdio
**Client:** Claude Code (Opus 4.6)

---

## Executive Summary

All 8 MCP tools are functional. 6 of 8 return useful, accurate results. `get_why` has a relevance problem — it returns decisions unrelated to the query. `search_codebase` returns results for nonsense queries with no indication they're low-quality matches. No tool crashed, but `get_why` experienced hangs in 2 of 4 test runs (file-path mode and health dashboard mode).

---

## Tool-by-Tool Results

### 1. `get_overview`

**Status:** Working
**Response time:** Fast (~2s)

**What it returned:**
- Project summary with accurate LOC count (52k) and file count (315)
- Correct tech stack breakdown (Python 50.2%, TypeScript 32.7%)
- Entry points (CLI main, server app, test fixtures)
- Architecture description with module breakdown
- Mermaid diagram (CLI -> Core -> Server dependency graph)
- Git health: 285 indexed files, 71 hotspots, bus factor 0.8, churn trend increasing

**Quality assessment:**
- Summary is accurate and well-structured
- Correctly identifies the monorepo structure (core, server, cli, web)
- Mentions the circular dependency between crud/database/models/mcp_server — this is genuinely useful context
- Top churn modules list is accurate

**Issues:**
- Entry points include test fixtures (`go_pkg/main.go`, `rust_pkg/src/lib.rs`) — these aren't real entry points, they're test data. A developer following this advice would start in the wrong place.
- The `packages/web` module is listed as a top churn module but the architecture section barely mentions it. Mismatch between git signals and documentation depth.

**Improvement ideas:**
- Filter test fixtures from entry points. An entry point should be something a developer actually runs, not test data.
- Weight the architecture summary toward high-churn areas — if `packages/web` is churning heavily, the overview should say more about it.

---

### 2. `get_context`

**Status:** Working
**Response time:** Fast (~2s)

**Tests run:**
| Input | Result |
|-------|--------|
| Single file (`main.py`) | Returned docs, symbols, importers, last_change |
| Multi-file (`models.py` + `parser.py`) | Both returned correctly in one call |
| `include` filter (`docs`, `ownership`, `freshness`) | Correctly filtered response fields |
| Nonexistent file (`NonExistentFile.py`) | Clean error: `"Target not found: 'NonExistentFile.py'"` |

**Quality assessment:**
- Documentation quality is high — the `parser.py` docs accurately describe the tree-sitter architecture, language config system, and even how to add a new language
- Symbol extraction is thorough — `parser.py` returned 35 symbols with correct signatures
- `imported_by` lists are accurate and useful for understanding coupling
- Ownership data works (`models.py` shows RaghavChamadiya at 100%)
- Freshness status works (`fresh` / `not stale`)

**Issues:**
- `parser.py` shows `primary_owner: null`, `contributor_count: 0`, `bus_factor: 0` — but the file clearly has commit history. Likely a git indexing gap where some files don't get ownership attributed.
- `last_change` returned `date: null, author: null, days_ago: 0` for `main.py` — this should have real data.

**Improvement ideas:**
- Investigate why some files have null ownership despite having git history. This is a data quality issue that undermines the `get_risk` tool too (it showed `owner: unknown` for files that definitely have an owner).
- `last_change` with all nulls is confusing — either return the actual data or omit the field. A null date with `days_ago: 0` is misleading (implies "changed today").

---

### 3. `search_codebase`

**Status:** Working
**Response time:** Fast (~1s)

**Tests run:**
| Query | Results | Quality |
|-------|---------|---------|
| `"how does the MCP server work?"` | mcp_cmd.py, scc-38 circular dep, architecture diagram | Good — top result is exactly right |
| `"authentication and API keys"` (filtered to file_page) | providers.py, openai.py, deps.py | Good — relevant files about provider/API key management |
| `"xyznonexistent gibberish query"` | search.py, search_cmd.py, GitMetadata | Bad — returned results for nonsense with no low-confidence signal |

**Quality assessment:**
- Semantic search works well for real queries — results are relevant and properly ranked
- `page_type` filter works correctly
- Relevance scores are present and ordered correctly
- Snippets are useful for quick scanning

**Issues:**
- **No empty results for nonsense queries.** The gibberish query returned 3 results with relevance scores of 2.4–3.4. There's no threshold or indicator telling the consumer "these results are probably not what you're looking for." An AI agent would treat these as real answers.
- `confidence_score` is always `1.0` across every result in every query. This field appears to be hardcoded and provides no actual signal.

**Improvement ideas:**
- Add a minimum relevance threshold. If the top result scores below some baseline, return an empty result set or add a `low_confidence: true` flag.
- Make `confidence_score` meaningful or remove it. A field that's always 1.0 is noise.
- Consider returning a `"no_relevant_results": true` flag when the best match score is below threshold.

---

### 4. `get_risk`

**Status:** Working
**Response time:** Fast (~2s)

**Tests run:**
| Input | Key findings |
|-------|-------------|
| `mcp_server.py` | 99% hotspot, churn-heavy, increasing, 2 co-change partners, 2414 lines added in 90d |
| `ui.py` + `git_indexer.py` (multi-target) | ui.py: 5% stable; git_indexer: 12% high-coupling, 3 co-change partners |
| `nonexistent/file.py` | Clean fallback: 0% hotspot, "no git metadata available" |

**Quality assessment:**
- **This is the strongest tool.** The data it returns is genuinely not derivable from reading source code.
- Hotspot scores are well-calibrated — `mcp_server.py` at 99% makes sense given the heavy recent development.
- Co-change partner data is actionable: knowing that `git_indexer.py` always changes with `init_cmd.py` and `models.py` helps predict blast radius.
- `risk_type` classification (churn-heavy, high-coupling, stable) provides quick triage.
- `impact_surface` showing the top downstream files by PageRank is useful.
- Global hotspots list provides ambient awareness regardless of what you query.
- Error handling for nonexistent files is clean.

**Issues:**
- `ui.py` shows `primary_owner: "unknown"` with `owner_pct: 0.0` — same null ownership issue as `get_context`. For risk assessment, knowing who owns a file is critical.
- `change_magnitude` shows `lines_added_90d: 0` for `git_indexer.py` — but this file was modified recently (commit `f749ca9` from this week). The 90-day window calculation may be off, or git indexing didn't capture recent commits.

**Improvement ideas:**
- Fix the ownership attribution gap. This is the #1 data quality issue across multiple tools.
- Verify that `change_magnitude` windows align with actual git history. A file committed this week showing 0 lines added in 90 days is wrong.

---

### 5. `get_why`

**Status:** Partially working (reliability + relevance issues)
**Response time:** Variable — fast when it works, hangs in some modes

**Tests run:**
| Input | Result |
|-------|--------|
| No args (health dashboard) | **Hung — had to cancel** |
| File path (`crud.py`) | **Hung — had to cancel** |
| NL query: `"why is the persistence layer using SQLite?"` | Returned 2 decisions + 3 related docs |
| NL query: `"why does the parser use tree-sitter?"` | Returned 8 decisions + 3 related docs |

**Quality assessment (when it works):**
- The `related_documentation` section works well — the tree-sitter query correctly surfaced `parser.py` as the top result with a high relevance score (16.9).
- The concept is valuable — surfacing architectural decisions that govern a piece of code is something no other tool does.

**However, the decisions themselves have a serious relevance problem:**
- Asked "why does the parser use tree-sitter?" → got decisions about Jinja2 templates, SQLite, streaming protocol, API key resolution order, token estimation heuristics. **None of these are about tree-sitter or the parser.**
- Asked "why is the persistence layer using SQLite?" → got a decision about Gemini's agentic loop. Not relevant.
- All decisions show `confidence: 0.6` and `source: "readme_mining"` — suggests these are mined from docs/READMEs with no filtering by actual relevance to the query.
- All decisions have `affected_files: []` — without file associations, the system can't filter decisions by what actually governs the queried code.

**Issues:**
1. **Hangs in file-path mode and health dashboard mode.** 2 of 4 calls had to be cancelled by the user. This is a blocker for production use.
2. **Decision relevance is poor.** The decisions returned don't match the query. It appears to return all known decisions rather than filtering to relevant ones.
3. **No `affected_files` populated.** This makes target-aware mode (`get_why` with both query and targets) likely non-functional since there's nothing to match against.
4. **All decisions are `"proposed"` status with 0.6 confidence.** No differentiation between strong architectural decisions and speculative observations.

**Improvement ideas:**
- **P0: Fix the hangs.** The file-path and dashboard modes need to complete or timeout gracefully.
- **P0: Filter decisions by relevance.** If the query is about tree-sitter, don't return decisions about Jinja2. Use the semantic search scores to threshold.
- **P1: Populate `affected_files`.** Without this, the tool can't answer "what decisions govern this file?" — which is its core value proposition.
- **P2: Differentiate decision confidence.** A decision extracted from an explicit ADR should score higher than one mined from a README sentence.

---

### 6. `get_dependency_path`

**Status:** Working
**Response time:** Fast (~1s)

**Tests run:**
| Source | Target | Result |
|--------|--------|--------|
| `cli/main.py` | `persistence/crud.py` | Path found: main.py -> init_cmd.py -> crud.py (2 hops) |
| `cli/main.py` | `web/sidebar.tsx` | No direct path — returned visual context |

**Quality assessment:**
- Direct path finding works correctly — the 2-hop chain through `init_cmd.py` is accurate.
- **The "no path found" response is excellent.** Instead of just saying "no path," it returns:
  - Nearest common ancestors with distances
  - Community analysis (source in community 26, target in community 91)
  - Bridge suggestions with PageRank scores
  - Human-readable suggestion: "both nodes connect through reindex_cmd.py"
- This makes the tool useful even when there's no direct dependency — it helps understand architectural silos.

**Issues:**
- The bridge suggestion points to `reindex_cmd.py` as the connection between CLI and web — this seems unlikely to be useful as an architectural insight. The CLI and web packages are intentionally separate.
- `external:@/lib/utils/cn` appears as a bridge suggestion with a high PageRank. External/alias nodes in the graph could be confusing.

**Improvement ideas:**
- When source and target are in completely different packages (Python CLI vs TypeScript web), consider saying "these are separate packages with no intended dependency path" rather than suggesting bridges. Not every pair of files should be connected.
- Filter or label external/alias nodes in suggestions.

---

### 7. `get_dead_code`

**Status:** Working
**Response time:** Fast (~2s)

**Tests run:**
| Parameters | Results |
|------------|---------|
| `tier: "high"` | 0 findings (none at high confidence) |
| `group_by: "directory"` | 21 medium-confidence findings across 11 directories |

**Quality assessment:**
- Summary stats are useful: 128 total findings, 420 deletable lines, 15 safe to delete.
- `kind` breakdown shows categories: 122 unreachable files, 6 zombie packages.
- Directory rollup is actionable: `tests/fixtures` has 4 findings (180 lines), `packages/core` has 8 (170 lines).
- `safe_to_delete` flag is conservative — good for production use.
- Individual findings include file path, confidence, reason, line count.

**Issues:**
- Test fixtures (`tests/fixtures/sample_repo/go_pkg/calculator/calculator.go`) are flagged as dead code with `reason: "File has no importers"`. These are test data files — they're not supposed to have importers. Same for `conftest.py` which is auto-loaded by pytest.
- `alembic/env.py` is flagged as dead — it's loaded by the Alembic migration framework, not imported. Framework-loaded files need special handling.
- All findings are medium confidence (0.7) with identical reasons. No differentiation.

**Improvement ideas:**
- Exclude known framework files: `conftest.py` (pytest), `alembic/env.py` (alembic), `__main__.py`, etc. These are entry points loaded by tools, not dead code.
- Exclude test fixture directories — or at least add a `is_test_fixture: true` flag so consumers can filter.
- Vary confidence scores based on additional signals (age, ownership, last commit date) rather than a flat 0.7.

---

### 8. `get_architecture_diagram`

**Status:** Working
**Response time:** Fast (~2s)

**Tests run:**
| Scope | Parameters | Result |
|-------|------------|--------|
| `repo` | Default | Markdown architecture doc with embedded mermaid |
| `module` | `packages/core`, `show_heat: true` | Detailed flowchart with churn heat annotations |

**Quality assessment:**
- Repo-level returns a well-structured architecture document (not just a diagram) — includes component descriptions, key modules, and the SCC note.
- Module-level diagram correctly shows all files in `packages/core` with their import relationships.
- Churn heat annotations work: `models.py` and `mcp_server.py` marked `hot`, `rust_pkg/src/models.rs` marked `warm`, most others `cold`. Matches `get_risk` data.
- Mermaid syntax is valid and renderable.

**Issues:**
- Repo-level scope returns the pre-generated architecture page (markdown + embedded mermaid), not a standalone diagram. This is fine but inconsistent with the module-level scope which returns pure mermaid.
- Module-level diagram for `packages/core` includes `packages/server/src/repowise/server/mcp_server.py` and `tests/fixtures/sample_repo/rust_pkg/src/models.rs` — these are outside `packages/core`. The diagram leaks across module boundaries because it follows imports.
- Node IDs in mermaid are very long (`packages_core_src_repowise_core_persistence_models_py`) making the rendered diagram hard to read.

**Improvement ideas:**
- Use short labels in the diagram (e.g., `models.py` or `persistence/models.py`) while keeping full paths in tooltips or node metadata.
- For module-scope diagrams, consider clipping edges at the module boundary with an "external" node rather than including the full external file.
- Make repo-level and module-level return consistent formats.

---

## Cross-Cutting Issues

### 1. Ownership data gaps
Multiple tools return `null` or `"unknown"` for file ownership on files that definitely have git history. This affects `get_context`, `get_risk`, and `get_dead_code`. **Root cause likely in git indexer** — some files may timeout during indexing or the ownership attribution logic has gaps.

**Impact:** High. Ownership is a key signal for risk assessment, code review routing, and dead code cleanup prioritization.

### 2. `confidence_score` is meaningless
In `search_codebase`, every result has `confidence_score: 1.0`. In `get_why`, every decision has `confidence: 0.6`. In `get_dead_code`, every finding has `confidence: 0.7`. These flat values provide no signal for consumers to differentiate quality.

**Impact:** Medium. AI agents consuming these tools have no way to judge result quality, leading to hallucination amplification — the agent treats a bad match the same as a good one.

### 3. Test fixtures pollute results
Test fixtures appear as entry points (`get_overview`), dead code (`get_dead_code`), and in dependency graphs (`get_architecture_diagram`). For a developer using these tools, test data showing up in architecture overviews is confusing.

**Impact:** Low-medium. Mostly a noise issue, but for public repos with large test suites this could significantly pollute results.

### 4. Error handling is solid
Every tool handles nonexistent inputs gracefully — no crashes, clean error messages, fallback data where appropriate. `get_dependency_path` is particularly good, providing visual context even when no path exists.

---

## Reliability Summary

| Tool | Functional | Response Time | Data Quality | Reliability |
|------|-----------|---------------|-------------|-------------|
| `get_overview` | Yes | Fast | Good (entry point issue) | Stable |
| `get_context` | Yes | Fast | Good (ownership gaps) | Stable |
| `search_codebase` | Yes | Fast | Good (no empty results for garbage) | Stable |
| `get_risk` | Yes | Fast | Good (ownership gaps, stale magnitude) | Stable |
| `get_why` | Partial | Variable | Poor (irrelevant decisions) | **Unstable — hangs** |
| `get_dependency_path` | Yes | Fast | Good | Stable |
| `get_dead_code` | Yes | Fast | Okay (false positives on fixtures) | Stable |
| `get_architecture_diagram` | Yes | Fast | Good (label readability) | Stable |

---

## Priority Fixes

### P0 (Blocking for production)
1. **`get_why` hangs** — file-path mode and health dashboard mode hang indefinitely. Must either fix or add timeouts.
2. **`get_why` decision relevance** — decisions returned are unrelated to queries. The tool's core promise is broken without this.

### P1 (Important for quality)
3. **Ownership attribution gaps** — files with git history showing `null` owner across multiple tools.
4. **Search confidence scoring** — nonsense queries should return empty results or be flagged as low-confidence.
5. **`affected_files` in decisions** — empty across all decisions, preventing target-aware decision lookup.

### P2 (Polish)
6. **Exclude test fixtures** from entry points, dead code findings, and architecture diagrams.
7. **Exclude framework-loaded files** (conftest.py, alembic/env.py) from dead code.
8. **Shorter node labels** in architecture diagrams for readability.
9. **Consistent response formats** between repo-level and module-level diagrams.
10. **Meaningful confidence scores** — vary based on actual signal strength rather than flat values.

---

## For Public Repo Demos (React, FastAPI, etc.)

The tools that will demo well today:
- **`get_overview`** — instant architecture summary of a massive codebase
- **`get_risk`** — "what are the riskiest files in React?" is a compelling demo
- **`get_context`** — deep file documentation with ownership and import graphs
- **`get_dependency_path`** — "how does the scheduler connect to the reconciler?"
- **`get_architecture_diagram`** with heat — visual, immediately understandable

The tools that need fixes before public demos:
- **`get_why`** — hanging will kill a live demo. Decision relevance issues will confuse the audience.
- **`search_codebase`** — works, but a nonsense query returning results in a demo would raise eyebrows.
- **`get_dead_code`** — flagging React's test fixtures as dead code would undermine credibility. For a repo with thousands of test files, false positives would dominate.
