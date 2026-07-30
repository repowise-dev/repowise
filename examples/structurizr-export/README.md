# Structurizr Export Example

Emit your architecture as [Structurizr](https://structurizr.com) DSL from the
graph Repowise already built. Deterministic — no LLM key, works after
`init --index-only`.

## Prerequisites

1. A git repository you can index locally.
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).
3. Index once (graph only is enough):

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Model fragment (default)

Writes a `model { … }` fragment you `!include` from your own `workspace.dsl`:

```bash
repowise export --format structurizr
# → typically .repowise/export/repowise-model.dsl (or path from --output)
```

Include it **inside** your workspace block:

```dsl
workspace "your name" {
    !include repowise-model.dsl

    views {
        systemContext sys_yourrepo {
            include *
            autolayout lr
        }
    }
}
```

Re-export as often as you like — the fragment regenerates; your views/styles stay yours.

## 2. Standalone workspace (no existing workspace.dsl)

When you do not already keep a hand-written workspace:

```bash
repowise export --format structurizr --standalone -o arch/
```

`--standalone` refuses to overwrite a `workspace.dsl` it did not write unless
you pass `--force`. Prefer the fragment once you own the presentation file.

## 3. Optional shaping flags

```bash
# One box per directory (component level)
repowise export --format structurizr --standalone --components

# Omit third-party / external systems
repowise export --format structurizr --no-externals
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise init --index-only --yes` | Completes without an API key |
| `repowise export --format structurizr` | Writes a `.dsl` fragment; prints include hint |
| `repowise export --format structurizr --standalone -o /tmp/rw-arch/` | Complete workspace that Structurizr can open alone |
| Zero containers in the printed counts | Index/graph problem — re-run init, not a format bug |

## Related docs

- [Structurizr DSL export](../../docs/architecture/structurizr-export.md)
- [CLI: export](../../docs/reference/CLI_REFERENCE.md)
