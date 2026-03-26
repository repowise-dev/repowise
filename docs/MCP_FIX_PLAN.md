# MCP Tools Fix Plan

Based on: [MCP_TOOLS_TEST_REPORT.md](./MCP_TOOLS_TEST_REPORT.md)

---

## Phase 1: Low-Hanging Fruit (Quick Wins)

These fixes are localized, low-risk, and can be done independently.

---

### 1.1 — Shorter node labels in architecture diagrams

**Issue:** Node IDs like `packages_core_src_repowise_core_persistence_models_py` are unreadable in rendered diagrams.

**File:** `packages/server/src/repowise/server/mcp_server.py` — lines 2347, 2353

**Current:**
```python
lines.append(f'    {src}["{e.source_node_id}"]')
```

**Fix:** Extract a short label (filename or `parent/filename`) from the full node ID:
```python
def _short_label(node_id: str) -> str:
    """e.g. 'packages/core/src/.../models.py' → 'persistence/models.py'"""
    parts = node_id.replace("\\", "/").split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]

# In diagram builder:
lines.append(f'    {src}["{_short_label(e.source_node_id)}"]')
```

**Effort:** ~15 min | **Risk:** None — cosmetic change

---

### 1.2 — Exclude test fixtures from entry points

**Issue:** `go_pkg/main.go` and `rust_pkg/src/lib.rs` from `tests/fixtures/` appear as entry points in `get_overview`.

**File:** `packages/server/src/repowise/server/mcp_server.py` — lines 477-484

**Current:** Queries `GraphNode.is_entry_point == True` with no test exclusion.

**Fix:** Add filter to exclude nodes under test/fixture directories:
```python
result = await session.execute(
    select(GraphNode).where(
        GraphNode.repository_id == repository.id,
        GraphNode.is_entry_point == True,
        GraphNode.is_test == False,  # Add this
    )
)
entry_nodes = result.scalars().all()

# Then also filter fixture paths in Python:
entry_nodes = [
    n for n in entry_nodes
    if "fixture" not in n.node_id.lower()
    and "test_data" not in n.node_id.lower()
]
```

**Also check:** `GraphNode` model has `is_test` column. Verify at:
- `packages/core/src/repowise/core/persistence/models.py` — `GraphNode` model
- `packages/core/src/repowise/core/ingestion/graph.py` — line ~65 where `is_test` is stored

**Effort:** ~20 min | **Risk:** Low

---

### 1.3 — Add framework-loaded files to dead code skip list

**Issue:** `conftest.py` (pytest), `alembic/env.py` (alembic), `__main__.py` flagged as dead code.

**File:** `packages/core/src/repowise/core/analysis/dead_code.py` — lines 71-77

**Current:**
```python
_NEVER_FLAG_PATTERNS = (
    "*__init__.py",
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
)
```

**Fix:** Extend the list:
```python
_NEVER_FLAG_PATTERNS = (
    "*__init__.py",
    "*__main__.py",        # Python entry points
    "*conftest.py",        # pytest auto-loaded
    "*alembic/env.py",     # Alembic migration framework
    "*manage.py",          # Django management
    "*wsgi.py",            # WSGI entry points
    "*asgi.py",            # ASGI entry points
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
)
```

**Effort:** ~10 min | **Risk:** None — adds safety, doesn't remove anything

---

### 1.4 — Exclude test fixture directories from dead code findings

**Issue:** Files in `tests/fixtures/sample_repo/` flagged as dead with `"File has no importers"`.

**File:** `packages/core/src/repowise/core/analysis/dead_code.py` — line 227

**Current:** Checks `is_test` flag but test fixtures aren't always tagged as test files (they're sample data, not test code).

**Fix:** Add fixture directory check in `_detect_unreachable_files`:
```python
# After the is_test check at line 227-228:
if self._is_fixture_path(str(node)):
    continue

# New method:
_FIXTURE_PATTERNS = ("fixture", "testdata", "test_data", "mock_data", "sample_repo")

def _is_fixture_path(self, path: str) -> bool:
    path_lower = path.lower()
    return any(pat in path_lower for pat in self._FIXTURE_PATTERNS)
```

**Effort:** ~15 min | **Risk:** Low — may miss some genuine dead code in fixture-named dirs, but that's the safer direction

---

### 1.5 — Remove or label hardcoded `confidence_score: 1.0` in search results

**Issue:** `confidence_score` is always `1.0` (from `Page.confidence` default) or `None`. Provides no signal.

**File:** `packages/server/src/repowise/server/mcp_server.py` — line 1743

**Current:** `item["confidence_score"] = info[0] if info else None` — reads from `Page.confidence` which defaults to `1.0`.

**Fix (option A — quick):** Remove the field entirely from search output since it's not useful:
```python
# Remove confidence_score from the output dict at line 1711
# Remove the batch lookup for confidence at lines 1718-1726
```

**Fix (option B — meaningful):** Replace with a normalized relevance indicator derived from the actual search score distribution:
```python
if output:
    max_score = max(item["relevance_score"] for item in output if item.get("relevance_score"))
    for item in output:
        raw = item.get("relevance_score", 0) or 0
        item["confidence_score"] = round(raw / max_score, 2) if max_score > 0 else 0.0
```

**Effort:** ~20 min | **Risk:** Low — consumers may check for this field, but it was always 1.0 so no one is branching on it

---

### 1.6 — Clip external nodes at module boundary in diagrams

**Issue:** Module-scoped diagram for `packages/core` includes files from `packages/server` and `tests/fixtures` because it follows imports across boundaries.

**File:** `packages/server/src/repowise/server/mcp_server.py` — around line 2343

**Current:** All edges in `relevant_edges` are rendered, including cross-module ones.

**Fix:** For module-scoped diagrams, replace external nodes with a single `external` stub:
```python
# When building the edge list for module scope:
for e in relevant_edges[:50]:
    src_id = e.source_node_id
    tgt_id = e.target_node_id
    # Clip cross-module edges
    if path and not src_id.startswith(path):
        src_id = "external"
    if path and not tgt_id.startswith(path):
        tgt_id = "external"
    # ... render with clipped IDs
```

**Effort:** ~30 min | **Risk:** Low

---

## Phase 2: Important Data Quality Fixes

These require more careful changes but fix the most impactful quality issues.

---

### 2.1 — `get_why` timeout wrapper for all modes

**Issue:** File-path mode and health dashboard mode hang indefinitely. 2 of 4 test calls had to be cancelled.

**Files:**
- `packages/server/src/repowise/server/mcp_server.py` — lines 1230-1269 (Mode 1), 1272-1333 (Mode 2), 1335-1474 (Mode 3)

**Root causes:**
1. **Mode 2 (path):** `all_git_meta` at line 1292-1297 loads entire `GitMetadata` table with no LIMIT. For large repos this is slow.
2. **`_git_archaeology_fallback`** at lines 1549-1574: Iterates all git metadata with an O(files × commits) inner loop. No timeout wrapper.
3. **Mode 3 (NL + targets):** Repeats the same unbounded git metadata load at lines 1443-1448.

**Fix — multi-layered:**

**A. Add overall timeout to the `get_why` handler:**
```python
@mcp.tool()
async def get_why(query: str | None = None, targets: list[str] | None = None, repo: str | None = None):
    try:
        return await asyncio.wait_for(_get_why_impl(query, targets, repo), timeout=30.0)
    except asyncio.TimeoutError:
        return {"error": "get_why timed out after 30s", "mode": "timeout", "query": query}
```

**B. Limit the `all_git_meta` query:**
```python
# Line 1292-1296: Add LIMIT
all_git_res = await session.execute(
    select(GitMetadata).where(
        GitMetadata.repository_id == repository.id,
    ).limit(500)  # Cap cross-file search space
)
```

**C. Add timeout to `_git_archaeology_fallback`:**
```python
async def _git_archaeology_fallback(...) -> dict:
    try:
        return await asyncio.wait_for(_git_archaeology_impl(...), timeout=10.0)
    except asyncio.TimeoutError:
        return {"triggered": True, "summary": "Git archaeology timed out.", ...}
```

**D. Limit the cross-file loop iteration:**
```python
# Line 1549: Cap iteration
for gm in all_git_meta[:500]:
```

**Effort:** ~1-2 hours | **Risk:** Medium — timeout values need tuning. Test with large repos.

---

### 2.2 — Filter `get_why` decisions by relevance

**Issue:** Query "why does the parser use tree-sitter?" returns decisions about Jinja2, SQLite, streaming protocol. Core promise broken.

**File:** `packages/server/src/repowise/server/mcp_server.py` — lines 1369-1375

**Root cause:** `_score_decision` (line 1477) does simple word overlap. Words like "use", "parser" match too broadly. Any decision mentioning common words gets a nonzero score, and all nonzero decisions are returned.

**Fix — add minimum score threshold:**
```python
# After scoring all decisions (line 1374):
scored_decisions.sort(key=lambda t: t[0], reverse=True)

# Add threshold: require at least 2 keyword matches worth of score
MIN_DECISION_SCORE = 3.0  # At least one title match or two field matches
scored_decisions = [(s, d) for s, d in scored_decisions if s >= MIN_DECISION_SCORE]

keyword_matches = [d for _, d in scored_decisions[:8]]
```

**Also improve `_score_decision`:**
```python
def _score_decision(d, query_words, target_files):
    if not query_words:
        return 1.0 if target_files else 0.0

    # Require at least 2 matching words for non-title fields (reduces noise)
    matching_word_count = 0
    for weight, text in fields:
        for word in query_words:
            if word in text:
                matching_word_count += 1
                score += weight

    # Penalize if only 1 word matched across all fields (too loose)
    if matching_word_count == 1 and score < 3.0:
        score *= 0.3

    return score
```

**Effort:** ~1 hour | **Risk:** Medium — threshold tuning needed. Too high → misses real matches.

---

### 2.3 — Search relevance threshold for nonsense queries

**Issue:** Gibberish query `"xyznonexistent gibberish query"` returns 3 results with scores 2.4-3.4 and no low-confidence indicator.

**File:** `packages/server/src/repowise/server/mcp_server.py` — after line 1715

**Fix:** Add a minimum relevance score threshold and a flag:
```python
output = output[:limit]

# Filter results below relevance threshold
MIN_RELEVANCE = 1.5  # Tuned: real matches typically score 3+
if output:
    top_score = output[0]["relevance_score"] if output[0].get("relevance_score") else 0
    if top_score < MIN_RELEVANCE:
        return {"results": [], "low_confidence": True,
                "message": "No results met the relevance threshold."}
```

**Alternative — softer approach:** Keep results but add a flag:
```python
LOW_CONFIDENCE_THRESHOLD = 2.0
low_confidence = all(
    (item.get("relevance_score") or 0) < LOW_CONFIDENCE_THRESHOLD
    for item in output
)
return {"results": output, "low_confidence": low_confidence}
```

**Effort:** ~30 min | **Risk:** Low — threshold needs calibration against real queries

---

### 2.4 — Fix ownership attribution gaps

**Issue:** Files with clear git history show `primary_owner: null` across `get_context`, `get_risk`, and `get_dead_code`.

**Files:**
- `packages/core/src/repowise/core/ingestion/git_indexer.py` — lines 250-262, 410-527

**Root causes identified:**
1. **45-second per-file timeout** (line 161, 250-254): If git blame takes >45s, file returns with all-null ownership fields. On Windows especially, blame can be slow for large files.
2. **Blame-only for files ≤100KB** (line 521): Large files skip blame entirely.
3. **Empty commit list** (line 437-439): If `_get_commits()` returns empty (e.g. file only in working tree, not committed), all fields stay null.

**Fix — multi-part:**

**A. Increase blame timeout or make it configurable:**
```python
_FILE_INDEX_TIMEOUT_SECS: float = 90.0  # Up from 45
```

**B. Ensure commit-based ownership is preserved even when blame fails:**
```python
# In _index_file, after lines 498-503 (commit-based owner set):
commit_owner_name = top_authors[0]["name"]
commit_owner_email = top_authors[0]["email"]
commit_owner_pct = top_authors[0]["commit_count"] / total_commits

# Lines 514-527: Only override if blame succeeds
try:
    blame_result = await self._get_blame_ownership(...)
    if blame_result[0] is not None:  # Only override if blame returned data
        meta["primary_owner_name"] = blame_result[0]
        ...
except Exception:
    pass  # Keep commit-based ownership as fallback
```

**C. On timeout, still save partial data:** Instead of returning just `{"file_path": file_path}` on timeout, save whatever partial ownership data was already computed.

**D. Surface `last_change` data properly** (related issue from test report):
- Check `get_context` handler for `last_change` with `date: null, days_ago: 0`
- Ensure `GitMetadata.last_commit_at` is populated even when blame fails

**Effort:** ~2-3 hours | **Risk:** Medium — timeout changes may slow init on large repos

---

### 2.5 — Populate `affected_files` for README-mined decisions

**Issue:** All README-sourced decisions have `affected_files: []`, making target-aware `get_why` non-functional.

**File:** `packages/core/src/repowise/core/analysis/decision_extractor.py` — lines 464-529

**Root cause:** `mine_readme_docs()` at lines 519-521 uses `_infer_modules_from_text()` for `affected_modules` but never sets `affected_files`. Unlike `scan_inline_markers()` (which calls `_get_neighbors()`) and `mine_git_archaeology()` (which uses `commit_files`), README mining has no file-level resolution.

**Fix:** After setting `affected_modules`, resolve module paths to actual files:
```python
# After line 521:
d.affected_modules = self._infer_modules_from_text(section_text)

# New: resolve modules to files
affected_files = set()
for mod in d.affected_modules:
    for node in self._graph.nodes():
        if str(node).startswith(mod + "/") or str(node) == mod:
            affected_files.add(str(node))
            if len(affected_files) >= 20:
                break
d.affected_files = list(affected_files)
```

**Effort:** ~1 hour | **Risk:** Medium — may produce noisy file lists if modules are broad

---

## Phase 3: Deeper Structural Improvements

These require more design consideration and testing.

---

### 3.1 — Meaningful confidence scores across tools

**Issue:** Flat confidence values: `1.0` in search, `0.6` in decisions, `0.7` in dead code. No differentiation.

**Current state by tool:**

| Tool | Source | Value | Location |
|------|--------|-------|----------|
| `search_codebase` | `Page.confidence` | Default `1.0`, linear decay over 30 days | `models.py:112` |
| `get_why` | `DecisionRecord.confidence` | Hardcoded per source type | `decision_extractor.py` |
| `get_dead_code` | Dead code analyzer | `1.0` / `0.7` / `0.4` by commit recency | `dead_code.py:259-265` |

**Fix approach:**

**A. Decisions:** Vary by source + evidence quality:
- `inline_marker` (code comments): 0.95 — developer explicitly wrote this
- `adr_file` (dedicated ADR docs): 0.90
- `git_archaeology` (commit messages): 0.70 — inferred, not explicit
- `readme_mining` (README text): 0.60 — weakest signal

Already partially implemented in `decision_extractor.py` — verify values are actually differentiated and flowing through.

**B. Dead code:** Add more signals to vary confidence:
```python
# Beyond just commit recency, factor in:
# - File age (older untouched files more likely dead)
# - Number of symbols (single-function files more likely dead)
# - Owner still active in repo (if owner left, higher chance dead)
```

**C. Search:** Already addressed in 1.5 — either remove the field or make it score-relative.

**Effort:** ~3-4 hours total | **Risk:** Medium — needs calibration

---

### 3.2 — Consistent response formats for `get_architecture_diagram`

**Issue:** Repo-scope returns markdown + embedded mermaid. Module-scope returns pure mermaid. Inconsistent.

**File:** `packages/server/src/repowise/server/mcp_server.py` — `get_architecture_diagram` handler

**Fix:** Standardize both to return the same structure:
```python
{
    "diagram_type": "flowchart",
    "mermaid_syntax": "...",
    "description": "...",
    "metadata": {
        "scope": "repo" | "module",
        "path": null | "packages/core",
        "node_count": 15,
        "edge_count": 23,
    }
}
```

For repo-scope, generate a mermaid diagram from the module-level graph rather than returning the pre-generated architecture page.

**Effort:** ~2 hours | **Risk:** Low-medium — repo-scope currently returns richer content (prose + diagram). Switching to pure diagram loses context. Consider returning both.

---

### 3.3 — `get_why` decision relevance: semantic filtering

**Issue:** Keyword scoring alone is too noisy. Words like "use" and "parser" match too broadly.

**Beyond the threshold fix in 2.2**, improve the actual scoring:

**A. Use the semantic search as primary ranker, not keyword:**
```python
# Current: keyword first, semantic second
# Better: semantic first, keyword to fill gaps

decision_results = await _decision_store.search(query, limit=10)
# Only fall back to keyword scoring if semantic search returns < 3 results
if len(decision_results) < 3:
    # keyword fallback...
```

**B. Require multi-word phrase matching, not just individual words:**
```python
# Instead of checking each word independently:
# "tree sitter" should match as a phrase, not "tree" and "sitter" separately
import re
bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
for bigram in bigrams:
    if bigram in text:
        score += weight * 2  # Phrase match worth more
```

**C. Negative signal: if decision text mentions the query topic 0 times but other topics 5+ times, it's irrelevant.**

**Effort:** ~3-4 hours | **Risk:** Medium — changes ranking behavior significantly, needs A/B comparison

---

### 3.4 — `change_magnitude` staleness in `get_risk`

**Issue:** `git_indexer.py` shows `lines_added_90d: 0` despite being committed this week.

**Root cause:** Git metadata is computed at `init`/`update` time. If the wiki hasn't been updated since the commits, the 90-day window is stale.

**Fix options:**
1. **Live git check:** On `get_risk` calls, optionally run a quick `git log --since=90.days` for the queried files to get fresh counts.
2. **Show data freshness:** Include `"data_as_of": "2026-03-20"` (last indexing time) so consumers know the data age.
3. **Periodic reindex:** Already exists via `update` command — document that `get_risk` data freshness depends on reindexing cadence.

**Effort:** ~2 hours for option 2 (quick), ~4 hours for option 1 (live) | **Risk:** Option 1 adds latency

---

## Fix Priority Matrix

| # | Fix | Phase | Effort | Impact | Dependencies |
|---|-----|-------|--------|--------|-------------|
| 1.1 | Short diagram labels | 1 | 15m | Low | None |
| 1.2 | Exclude test fixtures from entry points | 1 | 20m | Low-Med | Check GraphNode model |
| 1.3 | Framework files in dead code skip list | 1 | 10m | Low | None |
| 1.4 | Fixture dirs in dead code | 1 | 15m | Low | None |
| 1.5 | Fix/remove confidence_score in search | 1 | 20m | Med | None |
| 1.6 | Clip external nodes in diagrams | 1 | 30m | Low | None |
| 2.1 | `get_why` timeout wrapper | 2 | 1-2h | **High** | None |
| 2.2 | Decision relevance threshold | 2 | 1h | **High** | None |
| 2.3 | Search relevance threshold | 2 | 30m | Med | None |
| 2.4 | Ownership attribution gaps | 2 | 2-3h | **High** | git_indexer changes |
| 2.5 | Populate `affected_files` for README decisions | 2 | 1h | Med | decision_extractor |
| 3.1 | Meaningful confidence scores | 3 | 3-4h | Med | Multiple files |
| 3.2 | Consistent diagram response format | 3 | 2h | Low | None |
| 3.3 | Semantic decision filtering | 3 | 3-4h | Med | 2.2 first |
| 3.4 | Fresh change_magnitude data | 3 | 2-4h | Med | None |

---

## Recommended Execution Order

1. **Do Phase 1 all at once** — small, independent fixes across 4 files. ~2 hours total.
2. **Do 2.1 (timeouts) first in Phase 2** — this is the only reliability blocker.
3. **Do 2.2 + 2.3 (relevance thresholds)** — quick score gating.
4. **Do 2.4 (ownership)** — most impactful data quality fix, needs testing.
5. **Do 2.5 (affected_files)** — unlocks target-aware `get_why`.
6. **Phase 3 as time allows** — these are quality-of-life improvements.

---

## Files to Modify

| File | Fixes |
|------|-------|
| `packages/server/src/repowise/server/mcp_server.py` | 1.1, 1.2, 1.5, 1.6, 2.1, 2.2, 2.3 |
| `packages/core/src/repowise/core/analysis/dead_code.py` | 1.3, 1.4, 3.1 (dead code confidence) |
| `packages/core/src/repowise/core/ingestion/git_indexer.py` | 2.4 |
| `packages/core/src/repowise/core/analysis/decision_extractor.py` | 2.5, 3.1 (decision confidence) |
