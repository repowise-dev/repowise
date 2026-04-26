# Graph Algorithms in Repowise — Complete Guide

This document covers every graph algorithm used in Repowise: what it does, the intuition behind it, the actual math, and why Repowise chose it.

---

## The Foundation: What Is the Graph?

Before any algorithm runs, Repowise builds a **directed graph** from your codebase. The graph is two-tier: it contains both **file nodes** and **symbol nodes** in the same `networkx.DiGraph`, connected by different edge types.

**File-level nodes** (e.g., `auth/login.py`) represent source files. **Symbol-level nodes** represent individual functions, classes, methods, and interfaces extracted by the AST parser.

File-level edges:

| Edge type | Source | Example |
|-----------|--------|---------|
| `imports` (default) | Static import resolution from AST | `from auth import login` |
| `co_changes` | Git co-change analysis | `auth.py` and `config.py` frequently change together |
| `framework` | Framework-aware synthetic edges | pytest conftest→tests, Django admin→models, FastAPI include_router→routers, Flask register_blueprint→blueprints |

Symbol-level edges (all carry a `confidence` score from 0.0 to 1.0):

| Edge type | Source | Example |
|-----------|--------|---------|
| `DEFINES` | GraphBuilder `add_file()` | `login.py` DEFINES `validate_token` |
| `HAS_METHOD` | GraphBuilder `add_file()` | `AuthService` HAS_METHOD `login` |
| `CALLS` | `CallResolver` (3-tier resolution) | `validate_token` CALLS `hash_password` |

Framework edges are detected automatically when the tech stack includes Django, FastAPI, Flask, or pytest. They capture real runtime dependencies that static import resolution misses (e.g., `conftest.py` fixtures are loaded by pytest, not imported directly by test files).

**`file_subgraph()` — keeping metrics accurate across both tiers**

All centrality algorithms (PageRank, betweenness, SCCs, Louvain) operate on the file-level subgraph, not the full graph. `file_subgraph()` returns a view of the `DiGraph` containing only `file` and `package` nodes. Without this isolation, the large number of symbol nodes would inflate degree counts and distort every metric. Call `file_subgraph()` before running any file-level algorithm; call the full graph when you need symbol-level traversal.

This is the raw material. Every algorithm below operates on the file subgraph unless otherwise noted.

---

## 1. PageRank

### What question does it answer?

**"How important is this file to the codebase?"**

A file is important if many files depend on it, especially if _those_ files are also important.

### The intuition

Imagine a new developer joins the team. They pick a random file, start reading it, and follow one of its imports to the next file. They keep doing this — open a file, pick a random import, jump there. Over weeks of doing this, some files keep appearing again and again. Those files are the important ones.

PageRank simulates exactly this. It's a model of a random person browsing through the import graph, counting how often they land on each file.

### Why not just count importers?

Consider this graph:

```
main.py ──→ auth.py ──→ crypto.py
app.py  ──→ auth.py
cli.py  ──→ auth.py
test.py ──→ auth.py
```

`crypto.py` has only 1 importer (`auth.py`). `auth.py` has 4 importers. Simple count says `auth.py` is 4x more important.

But `crypto.py` is imported by `auth.py`, which is itself highly imported. So indirectly, all 4 files that need `auth.py` also transitively need `crypto.py`. A vote from `auth.py` should count more than a vote from `test.py` because `auth.py` is itself important.

PageRank captures this transitive importance. Simple import-counting does not.

### The math

Every file starts with a score of `1/N` where N is the total number of files. Then we iterate:

```
score(B) = (1 - α) / N  +  α × Σ [ score(A) / out_degree(A) ]
                                    for each A that imports B
```

Breaking this down:

- **`(1 - α) / N`** — the "teleport" component. Everyone gets this baseline. With α = 0.85, this is `0.15 / N`.
- **`α × Σ [ score(A) / out_degree(A) ]`** — the "follow edges" component. For each file A that imports B, B gets a share of A's score divided by how many files A imports.

**Why divide by `out_degree(A)`?** If `auth.py` imports 10 files, it splits its "vote" evenly across all 10. If it only imports 2 files, each gets a bigger share. A focused file that imports few things gives a stronger signal per import.

**Example iteration:**

Suppose 3 files, all start at score = 1/3 = 0.333:

```
A ──→ B ──→ C
```

Round 1:
- `score(A)` = 0.15/3 + 0 = 0.05 (nobody imports A)
- `score(B)` = 0.15/3 + 0.85 × (0.333/1) = 0.05 + 0.283 = 0.333
- `score(C)` = 0.15/3 + 0.85 × (0.333/1) = 0.05 + 0.283 = 0.333

Round 2 (using new scores):
- `score(A)` = 0.05 + 0 = 0.05
- `score(B)` = 0.05 + 0.85 × (0.05/1) = 0.05 + 0.0425 = 0.0925
- `score(C)` = 0.05 + 0.85 × (0.333/1) = 0.05 + 0.283 = 0.333

After many rounds, scores stabilize. C ends up highest (it's the most depended-on), A is lowest (nothing depends on it), B is in the middle.

### The damping factor α = 0.85

This is the probability of following an import edge vs. teleporting to a random file.

**Why teleport at all?** Two problems without it:

**Problem 1 — Disconnected components.** If your codebase has two isolated clusters with no imports between them, the random walker gets trapped in whichever cluster it starts in. Files in the other cluster get score = 0. That's wrong — they're still important within their cluster.

```
# Cluster A              # Cluster B
a1 ──→ a2 ──→ a3         b1 ──→ b2 ──→ b3
 ↑            │            ↑            │
 └────────────┘            └────────────┘

No edges between clusters. Without teleport, starting
in A means you never visit B. B's scores = 0.
```

**Problem 2 — Dead ends.** A file that imports nothing traps the walker. There are no edges to follow.

```
helpers.py ──→ (nothing)
```

The 15% teleport chance solves both: the walker occasionally jumps to a random file, so every file gets visited and scores are always nonzero.

**Why 0.85 specifically?** This is the original value from the Google PageRank paper. It's been validated across decades of graph analysis. Higher values (0.95) give more weight to the actual link structure but take longer to converge and are more sensitive to oddities. Lower values (0.5) spread scores out too evenly, making everything look similar. 0.85 is the standard tradeoff.

### Convergence failure

Sometimes the iterative computation oscillates and doesn't settle on stable scores. Repowise handles this:

```python
try:
    return nx.pagerank(filtered, alpha=alpha)
except nx.PowerIterationFailedConvergence:
    return {node: 1.0 / n for node in filtered.nodes()}
```

If PageRank doesn't converge, every file gets equal score `1/N`. This is safe — no file gets unfairly prioritized.

### Why co-change edges and symbol edges are excluded

Repowise's graph has several edge types. PageRank runs only on `imports` edges within the file subgraph. Why?

**Co-change** is noisy and doesn't indicate dependency. Examples of co-change that would corrupt PageRank:

- A developer renames a constant across 20 files in one commit. None of those files structurally depend on each other through that rename.
- Every bug fix touches `handler.py` and `test_handler.py`. The test file isn't architecturally important just because it changes alongside the handler.
- A release process updates `CHANGELOG.md`, `version.py`, and `setup.cfg` together. None import each other.

If co-change edges fed into PageRank, files that happen to change alongside many others would appear "important" even when nothing imports them. PageRank should answer "if this file breaks, what else breaks?" — that's a structural question, answered only by import edges.

**Symbol edges** (`DEFINES`, `HAS_METHOD`, `CALLS`) are also excluded from file-level PageRank for a different reason: they operate at a finer granularity than files. Including them would give outsized weight to files that define many small functions. `file_subgraph()` filters them out before any file-level metric runs. Symbol-level PageRank (when needed) runs on the full graph separately.

### How Repowise uses PageRank

1. **Documentation priority**: High PageRank files get wiki pages generated first. If budget is limited, the most depended-on files are documented.
2. **Generation depth**: Low PageRank + low git churn → "minimal" docs (saves LLM tokens and cost).
3. **Significant file selection**: Files above the PageRank threshold get detailed Level 2 wiki pages. Files below get summarized in module-level pages.
4. **CLAUDE.md generation**: When generating editor context files, files are sorted by PageRank descending — the most important files appear first for AI coding assistants.

---

## 2. Betweenness Centrality

### What question does it answer?

**"How critical is this file as a bridge between different parts of the codebase?"**

A file with high betweenness sits on the shortest paths connecting many pairs of files. If you removed it, parts of the codebase would become more disconnected.

### The intuition

Think of a road network. Some roads are highways that connect neighborhoods. If a highway closes, traffic between those neighborhoods has to take long detours. Other roads are cul-de-sacs — nobody drives through them to get somewhere else.

Betweenness centrality measures which files are the "highways." They may not be the most imported files (that's PageRank), but they're the ones that connect otherwise separate areas.

### How it differs from PageRank

Consider this graph:

```
          ┌── feature_a1.py
input.py ─┤── feature_a2.py ──→ bridge.py ──→ db_query.py ──→ models.py
          └── feature_a3.py                                ──→ schema.py
```

- `input.py` has high **PageRank** (many things depend on it indirectly)
- `bridge.py` has high **betweenness** (it's the only path connecting the feature cluster to the database cluster)
- `models.py` has high PageRank too (widely imported) but maybe low betweenness if there are other paths

**PageRank says "I'm important." Betweenness says "I'm a bottleneck."**

### The math

For each pair of nodes (s, t) in the graph:
1. Find all shortest paths from s to t
2. Count how many of those shortest paths pass through node v
3. Divide by the total number of shortest paths from s to t

```
betweenness(v) = Σ  [ σ_st(v) / σ_st ]
                s≠v≠t

where:
  σ_st   = total number of shortest paths from s to t
  σ_st(v) = number of those shortest paths that pass through v
```

Then normalize to [0, 1] by dividing by `(N-1)(N-2)` — the maximum possible pairs.

**Worked example:**

```
A ──→ B ──→ D
A ──→ C ──→ D
```

Shortest paths:
- A→D: two paths (A→B→D and A→C→D), length 2 each
- A→B: one path (A→B)
- A→C: one path (A→C)
- B→D: one path (B→D)
- C→D: one path (C→D)

For node B:
- Path A→D: 2 shortest paths total, 1 passes through B. Contribution: 1/2.
- Path C→D: 1 shortest path, none pass through B. Contribution: 0.
- B doesn't count as intermediate for paths starting/ending at B.

`betweenness(B)` = 1/2 (before normalization)

`betweenness(C)` = 1/2 (symmetric — same structure)

Both B and C are equally "bridge-like" because there are two parallel paths through the graph.

Now if we remove C:
```
A ──→ B ──→ D
```
`betweenness(B)` = 1 — B is the only bridge. All traffic flows through it.

### The complexity problem and sampling

The standard algorithm (Brandes' algorithm) is O(N × E) where N = nodes and E = edges. For a 50,000-file repo with 200,000 edges, that's 10 billion operations.

Repowise solves this with **sampling**:

```python
if n > 30_000:  # _LARGE_REPO_THRESHOLD
    k = min(500, n)
    return nx.betweenness_centrality(g, k=k, normalized=True)
```

Instead of computing exact betweenness using all N×N pairs, it samples 500 random source nodes and approximates. Research shows this gives good results — the ranking of "which files are most bridge-like" stays accurate even with sampling, just the exact scores shift slightly.

For repos under 30,000 nodes, exact computation is used.

### How Repowise uses betweenness

1. **Significant file selection**: A file with high betweenness gets Level 2 docs even if its PageRank isn't high. Bridge files deserve documentation because they're coupling points — if someone changes them, ripple effects cross component boundaries.
2. **File page context**: Betweenness is passed to the LLM in the file page template. The LLM can note "this file bridges the auth and database modules" in its generated documentation.
3. **Risk assessment**: High betweenness + high git churn = dangerous file. It's a bottleneck that changes frequently — a maintenance risk.

---

## 3. Strongly Connected Components (SCCs)

### What question does it answer?

**"Which files form circular dependency cycles?"**

### The intuition

In a healthy codebase, dependencies flow in one direction: `main → service → repository → model`. You can always tell which module is "higher" or "lower" in the stack.

A circular dependency breaks this. If `models.py` imports `serializers.py` and `serializers.py` imports `models.py`, there's no clear hierarchy. You can't understand one without the other. You can't test one without the other. You can't deploy one without the other.

An SCC is a maximal group of files where every file can reach every other file by following imports. If A can reach B and B can reach A, they're in the same SCC.

### The math (Tarjan's Algorithm)

The algorithm works via depth-first search (DFS) with two key numbers per node:

1. **Discovery index**: When you first visit a node, stamp it with an incrementing counter
2. **Low-link value**: The smallest discovery index reachable from this node via DFS edges + one back-edge

The algorithm:

```
1. Start DFS from any unvisited node
2. Push each visited node onto a stack
3. For each neighbor:
   - If unvisited: recurse, then update current node's low-link = min(own low-link, child's low-link)
   - If on stack: update current node's low-link = min(own low-link, neighbor's discovery index)
4. After processing all neighbors: if low-link == discovery index, this node is the "root" of an SCC.
   Pop everything from the stack down to this node — that's one SCC.
```

**Worked example:**

```
A ──→ B ──→ C ──→ A    (cycle: A, B, C)
           │
           └──→ D ──→ E    (no cycle)
```

DFS starting at A:
- Visit A (discovery=0, low=0), push A
- Visit B (discovery=1, low=1), push B
- Visit C (discovery=2, low=2), push C
  - C has edge to A (on stack): C.low = min(2, 0) = 0
  - C has edge to D: visit D
    - Visit D (discovery=3, low=3), push D
    - Visit E (discovery=4, low=4), push E
      - E has no unvisited neighbors
      - E.low == E.discovery → E is an SCC root. Pop E. SCC: {E}
    - D.low == D.discovery → D is an SCC root. Pop D. SCC: {D}
  - Back to C: C.low = 0
- Back to B: B.low = min(1, 0) = 0
- Back to A: A.low = 0, A.low == A.discovery → SCC root. Pop C, B, A. SCC: {A, B, C}

Result: Three SCCs: {A,B,C}, {D}, {E}. Only {A,B,C} has size > 1, meaning it's a circular dependency.

**Time complexity:** O(N + E) — each node and edge is visited exactly once. Very fast.

### What counts as a circular dependency

- **SCC of size 1**: Normal. A file that doesn't import itself. Not a cycle.
- **SCC of size > 1**: Circular dependency. These files form a cycle and can't be separated.

### How Repowise uses SCCs

1. **Dedicated wiki pages**: Each SCC with size > 1 gets its own `scc_page` in the wiki, documenting which files are in the cycle, what they import from each other, and why it's worth addressing.
2. **Architecture diagram**: Circular dependencies are highlighted in the generated architecture overview so the team can see coupling hotspots.
3. **Codebase health signal**: Many or large SCCs indicate tightly coupled code. The repo overview includes a count of circular dependencies.
4. **SCC IDs in node metadata**: Each file is tagged with an `scc_id`. Files in the same SCC share the same ID, which helps the frontend visualize which groups of files are tangled together.

---

## 4. Louvain Community Detection

### What question does it answer?

**"Which files naturally cluster together into subsystems?"**

### The intuition

In any codebase, files tend to cluster. The database layer files import each other heavily. The API route files import each other. But the database layer and the API routes have fewer connections between them.

Community detection finds these clusters automatically. It looks at the density of edges and groups files that are more connected to each other than to the rest of the graph.

Think of it as automated "package detection." Even if your code doesn't use clean package boundaries, community detection reveals the actual subsystem structure from the import graph.

### Why undirected?

Repowise converts the directed graph to undirected before running Louvain:

```python
communities = nx.community.louvain_communities(g.to_undirected(), seed=42)
```

Why? Import direction doesn't matter for clustering. If `auth.py` imports `crypto.py`, they're related — regardless of which one depends on which. The question isn't "who depends on whom" (that's PageRank's job) but "who belongs with whom."

### The math: Modularity

Louvain optimizes a metric called **modularity** (Q). Modularity measures the difference between actual intra-community edges and what you'd expect by random chance.

```
Q = (1/2m) × Σ [ A_ij - (k_i × k_j)/(2m) ] × δ(c_i, c_j)
              i,j

where:
  m = total number of edges
  A_ij = 1 if edge exists between i and j, 0 otherwise
  k_i = degree of node i (number of edges)
  k_j = degree of node j
  δ(c_i, c_j) = 1 if i and j are in the same community, 0 otherwise
```

**What each part means:**

- **`A_ij`**: Does an actual edge exist between file i and file j? (1 = yes, 0 = no)
- **`(k_i × k_j) / (2m)`**: If edges were distributed randomly, what's the probability of an edge between i and j? Files with many connections are more likely to connect by chance.
- **`A_ij - (k_i × k_j)/(2m)`**: The "surprise factor." Positive if there's an edge where you wouldn't expect one. Negative if there's no edge where you'd expect one.
- **`δ(c_i, c_j)`**: Only count pairs in the same community.

So modularity = sum of "surprising connections" within communities. High Q means communities have more internal edges than random chance would predict. That's what we want.

**Q ranges from -0.5 to 1.0.** Real-world values of 0.3 to 0.7 indicate meaningful community structure.

### The Louvain algorithm

The Louvain algorithm maximizes Q using a two-phase greedy approach:

**Phase 1 — Local moves:**
1. Start with each node in its own community (N communities)
2. For each node, compute the modularity gain of moving it to each neighbor's community
3. Move the node to the community that gives the biggest gain (if positive)
4. Repeat until no node wants to move

**The modularity gain formula:**

```
ΔQ = [ (Σ_in + k_i,in) / (2m) - ((Σ_tot + k_i) / (2m))² ]
   - [ (Σ_in / (2m)) - (Σ_tot / (2m))² - (k_i / (2m))² ]

where:
  Σ_in  = sum of edge weights inside the target community
  Σ_tot = sum of all edge weights of nodes in the target community
  k_i   = degree of node i
  k_i,in = sum of edges from node i to nodes in the target community
```

You don't need to memorize this. The key insight: it's cheap to compute (looks only at the node's neighbors), which is why Louvain is fast.

**Phase 2 — Aggregation:**
1. Collapse each community into a single super-node
2. Sum the edges between communities to create a smaller graph
3. Go back to Phase 1 on the smaller graph

This repeats until Q stops improving. Each round of aggregation discovers larger-scale communities. It naturally produces a hierarchy: first small clusters, then clusters-of-clusters.

**Time complexity:** Nearly O(N) in practice (each node is moved a small constant number of times). This makes it suitable for large codebases.

**Why `seed=42`?** Louvain is non-deterministic — the order you process nodes affects the result. Fixing the random seed makes results reproducible across runs. 42 is a conventional choice (from The Hitchhiker's Guide).

### How Repowise uses communities

1. **Architecture diagram**: Communities are listed in the generated architecture overview template, showing the LLM which files form natural subsystems so it can describe the high-level structure.
2. **Frontend graph visualization**: The graph UI has a "color by community" mode. Each community gets a distinct color, making clusters visually obvious.
3. **File page context**: Each file's community_id is included in the LLM prompt template when generating file documentation, helping the LLM explain which subsystem a file belongs to.
4. **Path finder diagnostics**: When no shortest path exists between two files, the system checks if they're in the same community. If they're in different communities, it suggests "bridge nodes" — files that have connections to both communities.

---

## 5. Shortest Path (BFS)

### What question does it answer?

**"How does file A depend on file B, through which intermediate files?"**

### The intuition

Given two files, trace the chain of imports connecting them. Like asking "how do I get from this API endpoint to that database model?" — the answer is a chain: `endpoint.py → service.py → repository.py → model.py`.

### The math

Repowise uses `nx.shortest_path()` which runs **Breadth-First Search (BFS)** on unweighted graphs:

1. Start at the source node. Mark it as visited. Distance = 0.
2. Visit all its neighbors. Mark them. Distance = 1.
3. Visit all _their_ unvisited neighbors. Distance = 2.
4. Continue until you reach the target or exhaust the graph.

BFS guarantees the first path found is the shortest (fewest hops). Time complexity: O(N + E).

### When no path exists: Visual context fallback

If no directed path exists from A to B, Repowise doesn't just say "not found." It computes diagnostic context:

1. **Reverse path check**: Maybe B → A exists (the dependency flows the other way).
2. **Nearest common ancestors**: Convert to undirected, find nodes reachable from both A and B, pick the closest ones. These are files that both A and B relate to even though they don't directly connect.
3. **Shared neighbors**: Files that are directly connected to both A and B.
4. **Community analysis**: Are A and B in the same community? If not, suggest bridge nodes (high-PageRank files connected to both communities).

This is a practical design choice — rather than a binary "connected or not," the system gives you the relationships that do exist.

---

## 6. Ego Graph (N-hop Neighborhood)

### What question does it answer?

**"What is the local context around this file?"**

### The intuition

When you're exploring a file, you want to see its neighborhood: what it imports, what imports it, and maybe one more level out. The ego graph gives you a focused subgraph centered on one file.

### The math

Uses `nx.ego_graph(graph, node, radius=hops, undirected=True)`:

1. Start at the center node
2. Find all nodes within `hops` steps in either direction (ignoring edge direction — both importers and importees count)
3. Return the subgraph induced by those nodes

With hops=2 (default), you see:
- The center file
- Everything it imports + everything that imports it (1-hop)
- Everything those files import or are imported by (2-hop)

This is BFS with a depth limit, time complexity O(branching_factor^hops).

---

## 7. Single-Source Shortest Path Length (BFS with cutoff)

### What question does it answer?

**"What is reachable from this entry point, and how far away is each file?"**

### Used in: Entry-point architecture view

```python
paths = nx.single_source_shortest_path_length(graph, ep.node_id, cutoff=3)
```

Starting from each entry point (e.g., `main.py`, `app.py`), this computes the distance to every reachable file within 3 hops. The union of all reachable files forms the "architecture view" — the parts of the codebase that are actually exercised from entry points.

Files NOT reachable from any entry point within 3 hops are candidates for dead code or library code that's only indirectly used.

This is standard BFS, truncated at depth 3.

---

## 8. In-Degree Analysis (Dead Code Detection)

### What question does it answer?

**"Is this file used by anything?"**

### The intuition

The simplest graph metric: count the number of incoming edges. If no file imports this file (in_degree = 0), it might be dead code.

### The math

```
in_degree(v) = |{ u : edge(u, v) exists }|
```

Just count the edges pointing into each node. O(1) per node lookup.

### Why it's not that simple

In-degree = 0 doesn't always mean dead code. Repowise applies several filters:

- **Entry points** (`main.py`, `app.py`, `index.ts`): Nothing imports them, but they're the starting points. Not dead.
- **Test files**: Tests import production code, not the other way around. Low in-degree is expected.
- **Config files** (`__init__.py`, `setup.py`, `next.config.js`): Loaded by frameworks, not via import statements.
- **Non-code files** (JSON, YAML, Markdown): No import semantics at all.
- **Framework patterns** (`*Handler`, `*Plugin`, `*Middleware`): Loaded dynamically, not via imports.

After filtering, Repowise scores confidence using git metadata:
- **No commits in 90 days + older than 180 days**: confidence = 1.0 (almost certainly dead)
- **No commits in 90 days**: confidence = 0.7 (probably dead)
- **Recent commits but no importers**: confidence = 0.4 (suspicious but maybe actively used via dynamic loading)

---

## How the Algorithms Connect

These algorithms aren't isolated — they feed into each other to create a complete picture:

```
                          Import Graph
                              │
           ┌──────────────────┼──────────────────┐
           │                  │                  │
       PageRank          Betweenness            SCCs
     "How important?"   "How bridge-like?"   "Any cycles?"
           │                  │                  │
           └──────┬───────────┘                  │
                  │                              │
          Significant File                  Cycle Documentation
          Selection (Level 2)               (SCC Pages)
                  │
                  ▼
           Doc Generation
           (priority order)


      Community Detection
      "What clusters exist?"
              │
     ┌────────┼────────┐
     │        │        │
  Arch     Frontend   Path Finder
  Diagram  Coloring   Bridge Suggestions


       In-Degree
       "Is anything importing this?"
              │
        Dead Code Detection
        (filtered by entry points, tests, configs)


       Shortest Path / Ego / BFS
       "How are specific files connected?"
              │
        API Endpoints + Frontend Graph Views
```

### Why these specific algorithms?

| Algorithm | Alternative considered | Why this one wins |
|-----------|----------------------|-------------------|
| PageRank | Simple import count | Captures transitive importance, not just direct |
| Betweenness | Closeness centrality | Betweenness directly identifies bottlenecks; closeness measures average distance which is less actionable |
| Tarjan's SCC | Brute-force cycle detection | O(N+E) vs O(N³). For large codebases, brute-force is impractical |
| Louvain | Spectral clustering, Girvan-Newman | Louvain is nearly O(N) and handles large graphs. Girvan-Newman is O(N²E), too slow. Spectral requires matrix decomposition, overkill for this use case |
| BFS shortest path | Dijkstra | Edges are unweighted (an import is an import). BFS is optimal for unweighted graphs and simpler than Dijkstra |

---

## Complexity Summary

| Algorithm | Time Complexity | Space | Repowise Optimization |
|-----------|----------------|-------|-----------------------|
| PageRank | O(E × iterations) ≈ O(E × 50) | O(N) | Converge fallback to uniform |
| Betweenness | O(N × E) | O(N + E) | Sample k=500 for repos > 30k nodes |
| SCCs (Tarjan) | O(N + E) | O(N) | None needed — already linear |
| Louvain | ~O(N) empirically | O(N + E) | Seed=42 for determinism |
| BFS shortest path | O(N + E) | O(N) | Cutoff=3 for entry-point views |
| In-degree | O(1) per node | O(1) | None needed |

For a 10,000-file repo with 40,000 edges, all metrics compute in under 5 seconds. For a 100,000-file repo, the sampling optimizations keep betweenness under 30 seconds while the rest stay fast.
