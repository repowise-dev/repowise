# Troubleshooting

Start with:

```bash
repowise doctor            # install, API keys, index drift, store health, wired agents
repowise doctor --repair   # fix what it safely can
repowise status            # what is indexed, and how far behind HEAD
```

`doctor --repair` reconciles drift between the database and the search stores,
re-registers a stuck Claude Code MCP entry, and refreshes the config of every
wired agent. It does not rewrite stale pages: `repowise update` does that.

## Install

**`repowise: command not found` after install.** The install directory is not
on `PATH`.

- uv: `uv tool update-shell`, then open a new shell. `uv tool dir --bin` prints
  the directory.
- pipx: `pipx ensurepath`, then open a new shell.
- pip: add the scripts directory under `python3 -m site --user-base` (`bin` on
  macOS and Linux, `Scripts` on Windows) to `PATH`.

Agent hosts start `repowise` by name, so restart the host after fixing `PATH`.

**"Provider X requires the 'Y' package".** Every provider SDK ships with
repowise, so this means a broken or partial install. `pip install <package>`
clears it at once; reinstalling repowise (`uv tool install --reinstall
repowise`, or `pip install --force-reinstall repowise`) fixes the cause.

**Garbled symbols or encoding errors on Windows.** The CLI switches its own
output to UTF-8 and substitutes a placeholder for any glyph the console cannot
draw, so a run should never stop on an encoding error. If a wrapper script or
an older shell still fails, set `PYTHONIOENCODING=utf-8` before running it
(`$env:PYTHONIOENCODING = "utf-8"` in PowerShell).

## Indexing

**init was interrupted, or finished with pages missing.** A provider outage
or rate limit can fail individual pages while the run completes.
`repowise init --resume` writes only the pages that are absent and makes no
model call for the ones you have, even if you switched provider.

**Doctor reports zero pages.** Even a keyless run writes pages, so an empty
wiki means the run did not finish. Run `repowise init --resume`.

**Memory or time on a very large repository.** Options, roughly in order of
effect:

```bash
repowise init --yes --no-prose --mode fast   # graph and essential git only; backfill later
REPOWISE_PARSE_WORKERS=2 repowise init       # fewer parse processes, less memory
repowise init -x vendor/ -x 'generated/**'   # exclude what you never edit
repowise init --max-file-pages 2000          # cap file pages, highest importance first
```

The parse pool defaults to at most eight worker processes; each one holds its
own parser, so lowering the count is the most direct memory lever.

**Indexing cost more than expected.** `repowise init --dry-run` shows the
estimate without writing a wiki, `--test-run` generates for the top 10 files
only, and `--skip-tests --skip-infra` narrows scope. Lower `--concurrency` if
you hit provider rate limits.

## Answers

**The agent does not see the repowise tools.** Restart the host; most read MCP
config only at startup. Then `repowise doctor --repair`, then re-run
`repowise agents add --target=<id> --yes` for your host.

**The agent answers from an old version of the code.** Every MCP response
carries the indexed commit and warns when it trails `HEAD`. Run
`repowise update`. To stop it recurring, keep the post-commit hook `init`
installs (`repowise hook install` puts it back) or run `repowise watch`. See
[Auto-Sync](../scale/AUTO_SYNC.md).

**An empty result.** Empty means "not found in what was analyzed", which is
not always "none exists". An empty caller list comes with a `*_basis` field
saying how much of that language's calls the graph resolved, and a response
whose `_meta` reports `degraded` failed to load part of the index. Read those
before concluding nothing calls a symbol or nothing is wrong.

**Semantic search returns nothing, or warns `embedder.mock_active`.** No real
embedder is configured, so search is full-text only. Set `REPOWISE_EMBEDDER`
(for example `gemini`, `openai` or `ollama`) and rebuild the vector store with
`repowise reindex`. `reindex` makes embedding calls only, no LLM calls, and
also repairs a corrupted vector store.

## FAQ

**Do I need an API key?** No. Without one, repowise builds the graph, git
history, code health, dead code, change risk and a full wiki rendered from
structure. Every MCP tool works; `get_answer` and `search_codebase` answer
from the structural pages. A key, or a keyless provider such as `ollama`,
`claude_cli` or `codex_cli`, adds model-written subsystem pages, decision
mining, `repowise ask` and dashboard chat.

**Where does the key go?** In `.repowise/.env`, which is gitignored.
`generate`, `update` and the MCP server load it. `repowise init` saves the key
it ran with there unless you pass `--no-save-key`.

**Will `--yes` spend money?** Only if a key is available and you did not pass
`--no-prose`. `repowise init --yes --no-prose` never calls a model.

**The dashboard shows no repositories.** The API and the dashboard must use
the same database. Check `REPOWISE_DB_URL` on the API and `REPOWISE_API_URL`
on the dashboard. `repowise serve` runs both together, so this only comes up
when you run them separately.

**How do I remove repowise?** `repowise uninstall --dry-run` lists everything
it wrote; `repowise uninstall` asks what to remove. The index is not selected
by default because rebuilding it is the expensive part.
