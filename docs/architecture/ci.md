# How CI runs

The `CI` workflow in `.github/workflows/ci.yml` runs on every pull request to
`main` and on every push to `main`. This page says which jobs run, what each
one checks, and how to reproduce a failure locally.

## Jobs

| Job | Checks | Reproduce locally |
|-----|--------|-------------------|
| Python 3.11, 3.12 and 3.13 tests | `tests/providers/` and `tests/unit/`; the 3.12 leg also measures coverage | `uv run pytest tests/providers/ tests/unit/ -n auto --dist loadfile` |
| tree-sitter floor | every language query compiles against the oldest supported tree-sitter | `uv pip install "tree-sitter==0.25.0"`, then `uv run --no-sync pytest tests/unit/ingestion/parser/test_query_compilation.py` |
| Windows smoke test | `repowise init` and `repowise status` under a cp1252 console | a Windows shell with `PYTHONIOENCODING` and `PYTHONUTF8` unset |
| Python lint (ruff) | `ruff check` with the rules in `pyproject.toml` | `uv run ruff check .` |
| Documentation drift | docs that name code which no longer exists | `uv run repowise doc-drift --check --baseline .github/doc-drift-baseline.json` |
| Repowise gates | health and security findings the change introduces (pull requests only) | `uv run repowise health check origin/main...HEAD` |
| Repowise coverage | patch coverage of the 3.12 report (pull requests only) | `uv run repowise coverage check origin/main...HEAD` |
| MCP response gate | MCP response sizes, on pull requests that touch the MCP tools or retrieval | `uv run pytest tests/integration/mcp/test_response_gate.py` |
| Integration tests | `tests/integration/` (push to `main` only) | `uv run pytest tests/integration/` |
| Web UI lint + type check | the web, ui, types, api-client and claude-mod packages | `npm ci`, then the `npm run` steps the job lists |
| VS Code extension test | type check, bundle size, webview tests, Electron smoke test | `npm run test --workspace packages/vscode` |
| Structurizr DSL parses | the C4 emitter's output, when the emitter changes | `uv run python scripts/emit_sample_dsl.py dsl-sample` |

## Installs and caches

- Every Python job installs with uv at a pinned version. The uv cache is keyed
  on `uv.lock` and the Python version, so a job restores the packages a
  previous run on `main` downloaded. A pull request that changes `uv.lock`
  downloads from scratch once.
- Ruff runs as the standalone binary at the version `uv.lock` pins, with no
  project install. Run the same version locally with `uv run ruff check .`.
- The Apex and COBOL grammars, which download on first use, are staged once per
  job with retries and cached on `uv.lock`.
