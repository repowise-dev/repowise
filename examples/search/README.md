# Search Example

Walk through `repowise search` — the default `auto` mode that picks a search
from the query's shape, concept search over the wiki (full text, plus semantic
matching when an embedder is configured), and symbol and path lookup. No LLM
key required.

## Prerequisites

1. A git repository with an indexed wiki.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).

```bash
cd /path/to/your-repo
repowise init --yes --no-prose
```

## 1. Default search (`--mode auto`)

```bash
repowise search "rate limiting"
repowise search "how are errors handled" --limit 5
```

`auto` is the default. It picks a mode from the shape of the query:

| The query looks like | Mode it runs | Example |
|----------------------|--------------|---------|
| A file path | `path` | `src/app/main.py` |
| A single identifier | `symbol` | `run_pipeline`, `pipeline` |
| Natural language that names an identifier | `hybrid` | `where is run_pipeline called` |
| Anything else | `concept` | `rate limiting` |

With `--format json` the `"mode"` field says which one ran. Concept search
runs over the rendered wiki pages and works immediately after index-only init.
Pass `--mode concept` (or one of the modes above) to choose yourself, for
example to search the wiki for a single word that `auto` would treat as a
symbol name.

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

`--mode fulltext` and `--mode semantic` are still accepted for older scripts.
Both run the same fused concept search as `--mode concept`, which matches on
full text alone when no embeddings exist.

## Workspace scoping

```bash
repowise search "auth" --repo backend     # one repo in a workspace
repowise search "auth" --all              # fan out across all repos
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise search "rate limiting" --limit 3` | Wiki hits with scores (or empty if no match) |
| `repowise search "rate limiting" --format json` | `"mode": "concept"` and the same hits as JSON |
| `repowise search "<symbol>" --mode symbol` | Symbol table with file + line |
| No index present | Clear error directing you to run `repowise init` |

## Related docs

- [CLI: `repowise search`](../../docs/reference/CLI_REFERENCE.md)
- [Quickstart](../../docs/start/QUICKSTART.md)
- [MCP `get_answer`](../../docs/agent/MCP_TOOLS.md) (synthesized Q&A in editors)
