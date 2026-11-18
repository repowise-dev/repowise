# How CI runs

The `CI` workflow in `.github/workflows/ci.yml` runs on every pull request to
`main`, on every push to `main`, and nightly on `main`. This page says which
jobs run when, what each one checks, and how to reproduce a failure locally.

## When each job runs

| Job | Pull request | Push to `main` and nightly |
|-----|--------------|----------------------------|
| Python 3.12 tests (shard 1/2, shard 2/2) | when Python-relevant files change | yes |
| Python 3.11 tests, Python 3.13 tests | no | yes |
| Python tests (summary) | always | always |
| tree-sitter floor, Windows smoke test, Python lint (ruff), Documentation drift | when Python-relevant files change | yes |
| Web UI lint + type check | when web files change | yes |
| VS Code extension test | when VS Code files change | yes |
| Repowise gates | always | no |
| Repowise coverage | when Python-relevant files change | no |
| MCP response gate | when the MCP tools or retrieval change | no |
| Integration tests | no | yes |
| Structurizr DSL parses | when the C4 emitter changes | when the C4 emitter changes (push only) |

## The check to require

`Python tests` is the one Python check a branch rule requires. It waits for
every Python test job, fails when any of them failed or was cancelled, and
passes when they all passed or the path filter skipped them. On pull requests
and pushes it also combines the shards' coverage into `coverage.xml`, which the
`Repowise coverage` job gates on, and a failed combine fails it. The nightly
run skips the combine, since nothing reads that coverage. Matrix job names
change with the matrix, so require `Python tests` and not a shard or a
version.

## Shards

On a pull request, the Python 3.12 unit tests run in two shards with coverage.
`--shard K/N` (defined in `tests/conftest.py`) keeps whole test files together,
so `--dist loadfile` still holds. Files listed in `tests/shard_weights.json` are
packed by their recorded seconds, heaviest first, into the lighter shard; any
other file goes by a hash of its path. Every run of the same files splits the
same way. `tests/providers/` runs in the first shard. Reproduce a shard locally
with:

```bash
uv run pytest tests/unit/ --shard 1/2 -n auto --dist loadfile
```

When one shard runs much longer than the other, refresh the weights from the
JUnit reports the shards upload (`junit-*` artifacts) and commit the result:

```bash
gh run download <run id> -p 'junit-*' -D junit
python scripts/shard_weights.py junit/*/junit.xml
```

## Path filters

The `Changed paths` job decides what a pull request needs. Pushes and the
nightly run ignore it and run everything.

- **Python**: every change except one confined to `packages/vscode/` or
  `packages/api-client/`. Python tests read docs, the web and ui TypeScript
  contracts, plugins and `.github` files, so those count as Python-relevant.
- **Web**: `packages/web`, `packages/ui`, `packages/types`,
  `packages/api-client`, `packages/claude-mod`, `plugins/claude-code` and
  `scripts/check-shared-imports.mjs`.
- **VS Code**: `packages/vscode`, `packages/ui`, `packages/types` and
  `packages/api-client`.
- **Shared**: `.github/workflows/ci.yml`, `package.json`, `package-lock.json`
  and `uv.lock` trigger every suite.

A skipped job reports as skipped, which a branch rule counts as passing.

## Reproducing a job locally

| Job | Command |
|-----|---------|
| Python tests | `uv run pytest tests/providers/ tests/unit/ -n auto --dist loadfile` |
| tree-sitter floor | `uv pip install "tree-sitter==0.25.0"`, then `uv run --no-sync pytest tests/unit/ingestion/parser/test_query_compilation.py` |
| Windows smoke test | `repowise init` and `repowise status` in a Windows shell with `PYTHONIOENCODING` and `PYTHONUTF8` unset |
| Python lint (ruff) | `uv run ruff check .` |
| Documentation drift | `uv run repowise doc-drift --check --baseline .github/doc-drift-baseline.json` |
| Repowise gates | `uv run repowise health check origin/main...HEAD` |
| Repowise coverage | `uv run repowise coverage check origin/main...HEAD` |
| MCP response gate | `uv run pytest tests/integration/mcp/test_response_gate.py` |
| Integration tests | `uv run pytest tests/integration/` |
| Web UI lint + type check | `npm ci`, then the `npm run` steps the job lists |
| VS Code extension test | `npm run test --workspace packages/vscode` |
| Structurizr DSL parses | `uv run python scripts/emit_sample_dsl.py dsl-sample` |

## Installs and caches

- Every Python job installs with uv at a pinned version. The uv cache is keyed
  on `uv.lock` and the Python version, so a job restores the packages a
  previous run on `main` downloaded. A pull request that changes `uv.lock`
  downloads from scratch once.
- Ruff runs as the standalone binary at the version `uv.lock` pins, with no
  project install. Run the same version locally with `uv run ruff check .`.
- The Apex and COBOL grammars, which download on first use, are staged once per
  job with retries and cached on `uv.lock`.
