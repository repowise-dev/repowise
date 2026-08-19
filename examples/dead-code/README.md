# Dead-Code Example

Walk through `repowise dead-code` — unreachable files, unused exports, and
cleanup candidates ranked by confidence. No LLM key required.

## Prerequisites

1. A git repository with indexed sources.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).
3. Index once (index-only is enough):

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Scan the repo

```bash
repowise dead-code
repowise dead-code --kind unused_export
repowise dead-code --safe-only --min-confidence 0.8
```

`--safe-only` keeps findings marked safe to delete. Raise `--min-confidence`
when you want a shorter, higher-trust list.

## 2. Filter and export

```bash
repowise dead-code --no-unreachable          # skip unreachable-file rows
repowise dead-code --no-unused-exports       # skip unused-export rows
repowise dead-code --format json | head      # machine-readable
repowise dead-code --format md > report.md   # shareable markdown (shell redirect; no -o flag)
```

In a workspace, scope to one repo:

```bash
repowise dead-code --repo backend
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise dead-code` | Table of findings (or empty if none) |
| `repowise dead-code --safe-only --min-confidence 0.8` | Subset with safe-to-delete markers |
| `repowise dead-code --format json` | JSON array; no API key needed |

## Related docs

- [Dead code layer](../../docs/layers/DEAD_CODE.md)
- [CLI: `repowise dead-code`](../../docs/reference/CLI_REFERENCE.md)
- [Quickstart](../../docs/start/QUICKSTART.md)
