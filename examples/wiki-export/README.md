# Wiki Export Example

Export indexed wiki pages to static files with `repowise export`. Supports
markdown, HTML, and JSON — no LLM key required. For Structurizr DSL, see
[structurizr-export/](../structurizr-export/).

## Prerequisites

1. A git repository with `repowise init` completed (index-only is enough).
2. `repowise` on `PATH` (`uv tool install repowise` or from this repo:
   `uv sync --all-packages`).

```bash
cd /path/to/your-repo
repowise init --index-only --yes
```

## 1. Export wiki pages

```bash
repowise export                                    # markdown → .repowise/export/
repowise export --format markdown -o ./wiki-export
repowise export --format html -o ./wiki-html
repowise export --format json -o ./wiki-json    # writes ./wiki-json/wiki_pages.json
```

## 2. Host or archive offline

Markdown and HTML exports are ordinary files — commit them, publish to static
hosting, or attach to a release. JSON is useful for tooling that reads the
wiki programmatically.

```bash
ls ./wiki-export | head
ls ./wiki-html | head
ls ./wiki-json/wiki_pages.json
```

## Smoke checklist

| Step | Expected |
|------|----------|
| `repowise export --format markdown -o /tmp/wiki-md` | Directory of `.md` files |
| `repowise export --format html -o /tmp/wiki-html` | Directory of `.html` files |
| `repowise export --format json -o /tmp/wiki-json` | `/tmp/wiki-json/wiki_pages.json` created |
| No index present | Error: run `repowise init` first |

## Related docs

- [CLI: `repowise export`](../../docs/reference/CLI_REFERENCE.md)
- [Structurizr export example](../structurizr-export/)
- [User guide](../../docs/start/USER_GUIDE.md)
