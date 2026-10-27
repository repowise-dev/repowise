# repowise-cli

The `repowise` command-line interface: `init`, `update`, `serve`, `mcp`, `risk`,
`health`, `dead-code`, `coverage`, `distill` and the rest of the commands.

**Python >= 3.11 · AGPL-3.0-or-later**

## Where it sits

This is one of the Python packages in the repowise monorepo. It drives the
pipelines in `packages/core` and starts the API and MCP servers from
`packages/server`. Commands live in `src/repowise/cli/commands/`, and the entry
point is `src/repowise/cli/main.py`.

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

- Every command and flag: [docs/reference/CLI_REFERENCE.md](../../docs/reference/CLI_REFERENCE.md)
- How the system is built: [docs/architecture/](../../docs/architecture/)
