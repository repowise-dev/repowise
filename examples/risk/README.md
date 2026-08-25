# Change-Risk Example

Rank a commit or `base..head` range against recent repository changes with
`repowise risk`. No LLM key required — pure git + calibrated signals.
Works without `repowise init` (index is optional).

## Prerequisites

1. A git repository with at least one commit.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).

```bash
cd /path/to/your-repo
```

## 1. Score your current change, a commit, or a range

```bash
repowise risk                 # score uncommitted work, else HEAD
repowise risk HEAD            # score the last commit
repowise risk main..HEAD      # whole branch / PR as one change
repowise risk HEAD~5..HEAD    # recent local work
```

The headline is **fix history**: which of the touched files have needed bug
fixes before, recency-weighted, and where that sits among this repo's own
recent commits. Below it, the diff shape — percentile and review priority
(`Below typical` / `Typical` / `Elevated`) among recent commits, and the 0–10
score, which measures how large and spread out the change is rather than how
dangerous.

## 2. Narrow what counts

```bash
# Only certain suffixes
repowise risk --ext .py
repowise risk main..HEAD --ext .ts,.tsx

# Omit paths (repeatable); root .riskignore also applies
repowise risk main..HEAD -x 'tests/' -x '*.spec.ts'
```

## 3. Machine-readable output

```bash
repowise risk main..HEAD --format json
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise risk` | Table led by percentile/review priority, with a supporting diff-shape score |
| `repowise risk main..HEAD` | Same shape for the range (or a clear revspec error) |
| `repowise risk --format json` | JSON object; no API key needed |

## Related docs

- [Change risk](../../docs/layers/CHANGE_RISK.md)
- [CLI: `repowise risk`](../../docs/reference/CLI_REFERENCE.md)
- [Quickstart](../../docs/start/QUICKSTART.md)
