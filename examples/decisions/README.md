# Decisions Example

Walk through `repowise decision` — list architectural decisions, review
proposals, and read the health dashboard. No LLM key required for list/health.

## Prerequisites

1. A git repository with an index (`repowise init`, index-only is enough).
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

Decisions are mined during indexing from inline markers, commits, and other
sources documented in [DECISIONS.md](../../docs/layers/DECISIONS.md).

## 1. List and filter

```bash
repowise decision list
repowise decision list --proposed       # proposals needing review
repowise decision list --status active
repowise decision list --stale-only
repowise decision show <id>             # full record for one id
```

## 2. Health dashboard

```bash
repowise decision health
```

Shows active vs proposed counts, stale decisions, and hotspots that lack
governing decisions.

## 3. Curate records (interactive)

```bash
repowise decision add                   # interactive add
repowise decision confirm <id>          # accept a proposal
repowise decision dismiss <id>          # reject; won't be re-proposed
repowise decision deprecate <id>        # mark superseded
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise decision list` | Table of decisions (or "No decisions found") |
| `repowise decision list --proposed` | Proposed rows only, or empty |
| `repowise decision health` | Counts + ungoverned hotspot paths |

## Related docs

- [Decisions layer](../../docs/layers/DECISIONS.md)
- [CLI: `repowise decision`](../../docs/reference/CLI_REFERENCE.md)
- [MCP `get_why`](../../docs/agent/MCP_TOOLS.md)
