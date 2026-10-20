# repowise-core

The engine behind repowise: ingestion and parsing, the dependency graph, git
history, health and dead-code analysis, decisions, wiki generation, providers
and persistence. Every other Python package depends on it.

**Python >= 3.11 · AGPL-3.0-or-later**

## Where it sits

This is one of the Python packages in the repowise monorepo. Code lives under
`src/repowise/core/`; the main areas are `ingestion/`, `analysis/`,
`generation/`, `persistence/`, `pipeline/` and `providers/`.
`packages/cli` and `packages/server` call into it.

It is not published to PyPI on its own. Users get it inside the single
`repowise` distribution built from the root `pyproject.toml`.

## Install the product

```bash
uv tool install repowise      # or: pipx install repowise / pip install repowise
cd your-repo
repowise init
```

## Develop on it

From the repository root, `uv sync --all-packages` installs every package in
editable mode. See [CONTRIBUTING.md](../../.github/CONTRIBUTING.md).

## Documentation

- How the system is built: [docs/architecture/](../../docs/architecture/), starting with
  [ARCHITECTURE.md](../../docs/architecture/ARCHITECTURE.md)
- Adding a language: [docs/architecture/language-support.md](../../docs/architecture/language-support.md)
- CLI commands that drive it: [docs/reference/CLI_REFERENCE.md](../../docs/reference/CLI_REFERENCE.md)
