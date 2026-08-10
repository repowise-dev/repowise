# Search Example

Walk through `repowise search` — keyword lookup over the wiki, semantic
search when an embedder is configured, and symbol lookup by name. No LLM
key required for fulltext and symbol modes.

## Prerequisites

1. A git repository with an indexed wiki.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Full-text search (default)

```bash
repowise search "rate limiting"
repowise search "pipeline" --limit 5
```

Searches rendered wiki pages. Works immediately after index-only init.

## 2. Symbol search

```bash
repowise search "run_pipeline" --mode symbol
repowise search "AuthService" --mode symbol --limit 10
```

Resolves indexed symbols to file paths and line numbers.

## 3. Semantic search (optional)

Requires a configured embedder (set during `init` / `reindex`):

```bash
repowise search "how are errors handled" --mode semantic
```

Without embeddings, use `--mode fulltext` explicitly or rely on the default.

## Workspace scoping

```bash
repowise search "auth" --repo backend     # one repo in a workspace
repowise search "auth" --all              # fan out across all repos
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise search "pipeline" --limit 3` | Wiki hits with scores (or empty if no match) |
| `repowise search "<symbol>" --mode symbol` | Symbol table with file + line |
| No index present | Clear error directing you to run `repowise init` |

## Related docs

- [CLI: `repowise search`](../../docs/reference/CLI_REFERENCE.md)
- [Quickstart](../../docs/start/QUICKSTART.md)
- [MCP `get_answer`](../../docs/agent/MCP_TOOLS.md) (synthesized Q&A in editors)
