# Distill — index-aware output distillation

AI coding agents burn most of their context window on *output they never
needed*: 300 lines of passing-test dots to find 4 failures, a full `git log`
to learn "what changed recently", a 60k-token diff to review one hunk. Distill
compresses that output **before the agent reads it** — errors first, structure
preserved, everything reversible.

Distill is a **capability**, not a sixth intelligence layer: it reuses the
index the five layers already build (symbol bounds, graph centrality,
hotspots) to decide *what to keep*, instead of compressing blind.

```bash
repowise distill pytest -x        # run pytest, print a compact errors-first rendering
repowise expand a1b2c3d4e5f6      # restore anything that was omitted
repowise saved                    # tokens & dollars saved so far
```

**Guarantees** (enforced by the engine, asserted by tests):

- **Errors always survive.** Every error/failure-classified line in the raw
  output appears in the distilled rendering.
- **Fully reversible.** Raw output is stored *before* any marker is emitted;
  `repowise expand <ref>` round-trips it byte-for-byte.
- **Fallback to raw.** Any filter error, storage failure, or non-improvement
  prints the original output unchanged. Distillation can never lose output.
- **Net-positive only.** Output is only distilled when it actually gets
  smaller (marker included) — small outputs pass through untouched.
- **Exit codes preserved.** `repowise distill <cmd>` is a drop-in wrapper in
  scripts and agent tool calls alike.

---

## The surfaces

### 1. `repowise distill <cmd>` — the executor

Runs the command (shell semantics preserved), captures stdout+stderr, picks a
filter by command shape (then by content sniff), and prints the compact
rendering. Eight filters ship:

| Filter | Commands | What it keeps |
|---|---|---|
| `test_output` | pytest, jest, vitest, cargo test, go test | failures + assertion details + summary; collapses pass parades |
| `build_output` | npm/tsc/cargo/go builds | errors and warnings grouped; strips progress/boilerplate |
| `git_status` | `git status` | porcelain-style compact status |
| `git_log` | `git log` | recent subjects + counts |
| `git_diff` | `git diff`/`show` | stat + the most relevant hunks |
| `search_results` | grep / rg floods | grouped-by-file digest with per-file counts and anchors |
| `file_listing` | ls / tree / find | grouped tree rendering |
| `logs` | anything log-shaped | template-collapse with counts (timestamps/ids normalized) |

Dropped content is stored in the omission store and referenced inline:

```
[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); restore: repowise expand a1b2c3d4e5f6]
```

### 2. `repowise expand <ref>` — the reversal

```bash
repowise expand a1b2c3d4e5f6              # full original output
repowise expand a1b2c3d4e5f6 -q "FAILED"  # only the matching lines
repowise expand "[repowise#a1b2…]"        # a pasted whole marker works too
```

Looks in the current repo's store first, then the user-level fallback store.
MCP clients without a shell can resolve the same refs through
`get_symbol("repowise#<ref>")` — see [MCP](#5-mcp-response-budget--_metaomitted).

### 3. The command-rewrite hook (Claude Code)

Opt-in. A PreToolUse hook rewrites noisy agent commands —
`pytest -x` → `repowise distill pytest -x` — **pending your approval**: the
default posture is `ask`, so Claude Code shows you the modified command before
running it. Install:

```bash
repowise hook rewrite install      # or opt in during `repowise init`
repowise hook rewrite status
repowise hook rewrite uninstall    # removes only the repowise entry
```

The hook is deliberately conservative. It never rewrites:

- pipes, redirections, compound commands (`|`, `>`, `&&`, `;`, backticks, `$()`)
- watch/follow modes (`--watch`, `tail -f`, …)
- anything on the trivial-command ignore-list, or already-prefixed commands
- commands in repos that have not opted into repowise (no `.repowise/` upward)

Per-repo behavior is configured under `distill.commands` in
`.repowise/config.yaml` — see [Configuration](#configuration). Declining the
`repowise init` prompt writes `distill.commands.enabled: false`, so a hook
installed globally from another repo stays inert in this one. The hook answers
in well under 100 ms (stdlib-only hot path, no database).

`repowise init` also adds a short "Output Distillation" section to the managed
`CLAUDE.md`, teaching the agent to prefer `repowise distill <cmd>` voluntarily
and to expand markers instead of re-running commands — this works in **any**
agent that runs shell commands, hook or no hook.

### 4. Read intelligence — skeletons and stale-read notices

The index knows every symbol's line bounds, so repowise can render a **file
skeleton** — every signature, imports, and the bodies of only the most central
symbols — without parsing anything at query time:

```
get_context(["src/big_module.py"], include=["skeleton"])
```

A typical 600-line file skeletonizes to ~15% of its full tokens with every
signature present. Body selection is importance-ranked (symbol PageRank,
hotspot bit, query match) — this is where the index makes distillation
smarter than blind truncation.

The existing PostToolUse hook complements this passively:

- **Skeleton nudge** — after a large `Read` of an indexed file, the agent is
  told the skeleton's token cost vs the full file (once per file per session).
- **Stale-read notice** — after an `Edit`/`Write`, a later `Read` of the same
  file warns that earlier excerpts predate the edit.
- **Search digest** — grep floods (≥50 lines) get a compact grouped-by-file
  digest ordered by graph centrality.

### 5. MCP response budget — `_meta.omitted`

All MCP tool responses were always token-budgeted; before Distill, truncation
was silent. Now every drop goes through the same omission store:

```jsonc
"_meta": {
  "omitted": {
    "refs": ["a1b2c3d4e5f6"],
    "tokens": 5840,
    "restore": "repowise expand <ref> (CLI) or get_symbol(\"repowise#<ref>\", query?) (MCP)"
  }
}
```

`get_symbol` resolves omission refs as well as symbol ids — the
`repowise#<12-hex>` shape is unambiguous next to `path/to/file.py::Name`, and
the optional `query` parameter searches within the stored content. Tool count
stays at nine. See [MCP_TOOLS.md](MCP_TOOLS.md).

> **Note:** MCP truncation is *not* counted in the savings ledger — those
> responses were always capped, so nothing was "saved" relative to before.
> `repowise saved` covers the distill command/hook path only.

---

## The omission store

`.repowise/omissions/omissions.db` — a SQLite sidecar (WAL), deliberately
separate from `wiki.db` so hook-time writes never contend with indexing.
Falls back to `~/.repowise/omissions/` when the current directory is not
inside a repowise repo.

- Content is keyed by a 12-hex truncated SHA-256 — the same ref that appears
  in markers, so one store serves the CLI, the hook, and MCP.
- **Durable across sessions** by design: an agent resuming work tomorrow can
  still expand yesterday's markers.
- Pruning is TTL + size-cap based (`7 days` / `50 MB` by default, configurable),
  applied opportunistically on write. The most recent row is never evicted, so
  a just-rendered marker cannot dangle.

---

## `repowise saved` — the savings report

```bash
repowise saved                  # per-filter rollup + totals + est. dollars
repowise saved --by day         # daily rollup
repowise saved --by source      # cli vs hook
repowise saved --since 2026-06-01
repowise saved --model claude-opus-4-6   # price the estimate differently
```

Dollar estimates price saved tokens at the chosen model's *input* rate (saved
tokens are input the agent never had to read) using the same pricing table as
`repowise costs`. Token counts are chars/4 estimates.

The report covers the **distill command/hook path only** — MCP response
truncation is intentionally not in the ledger (see above).

The local dashboard mirrors this: the Costs page's *Cache & savings* tab shows
a Distill savings card with the same rollup.

---

## Measured savings

On a public OSS repository (microdot), one run per command, tokens estimated
chars/4 — the same estimator the ledger uses:

| Command | Raw tokens | Distilled | Saved |
|---|---:|---:|---:|
| `pytest -q` (11 failures) | 3,374 | 1,317 | **61%** — all 11 `FAILED` lines preserved |
| `git log -50` | 3,064 | 331 | **89%** |
| `git diff` (30 commits of history) | 62,833 | 8,635 | **86%** |
| `git log --oneline -30` | 321 | 321 | 0% — already compact, passed through |
| `git status` (clean tree) | 83 | 83 | 0% — too small to distill, passed through |

The 0% rows are the net-positive guard working: distill never bloats small
output. In an end-to-end agent spot-check on the same repo (a seeded
11-failure bug), the agent diagnosed the exact root-cause line and fix from
the distilled test output — identical conclusion to the raw-output run.

Fixture-suite medians across the seven core filters: ≥60% reduction on
test/build output with zero error-line loss (asserted in CI).

---

## Configuration

The `distill:` block in `.repowise/config.yaml`:

```yaml
distill:
  enabled: true                  # master switch for this repo
  commands:
    enabled: true                # the command path (CLI + hook rewrites)
    permission: ask              # ask | allow | off — hook posture
    families:                    # per-filter overrides
      test_output: allow         # auto-allow rewrites for test runs
      git_diff: deny             # never rewrite git diff here
    disabled_filters: []         # filters to skip entirely, e.g. [logs]
  omission_store:
    ttl_days: 7                  # prune stored omissions after this
    max_mb: 50                   # size cap, oldest pruned first
```

Everything defaults sensibly with no block present. `repowise doctor`
validates the block (unknown keys, bad permission values, unknown filter
names, non-positive store sizing) and reports the store size against its cap
and whether the rewrite hook is installed.

---

## Safety model, in one place

| Risk | Mitigation |
|---|---|
| A filter eats a critical line | errors-first invariant + fixture tests + `expand` recovery + fallback-to-raw |
| Silent permission escalation | rewrites default to `ask`; the user sees the modified command |
| Marker with nothing behind it | content stored *before* the marker renders; store failure ⇒ raw output |
| Compound-command semantics | pipes/redirects/`&&` are never rewritten |
| Unindexed or stale repo | filters work index-free; index only improves ranking |
| Store growth | TTL + size cap, pruned on write; `repowise doctor` reports size |

---

## See also

- [CLI_REFERENCE.md](CLI_REFERENCE.md) — `distill`, `expand`, `saved`, `hook rewrite`
- [MCP_TOOLS.md](MCP_TOOLS.md) — `_meta.omitted`, skeleton include, `get_symbol` ref overload
- [CONFIG.md](CONFIG.md) — the `distill:` block
- [INTELLIGENCE_LAYERS.md](INTELLIGENCE_LAYERS.md) — the five layers whose index Distill reuses
