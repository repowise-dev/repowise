# Offline, CI and benchmarks

Repowise builds its whole index on your machine with no API key and no model
calls. That makes it free to run in CI, in an air-gapped network, and in a
benchmark harness that indexes many repositories.

## Free by default

```bash
repowise init --yes --no-prose
```

`--no-prose` builds the dependency graph, git history (hotspots, ownership,
co-change), code health, dead code, change risk, documentation built from
code, decisions from markers and ADRs, and full-text and symbol search. It
makes zero model calls, even when an API key is set in the environment.

A keyless index of envoy, about 1M lines of C++, built in about 10 minutes
for $0.07 of cloud compute.

Every MCP tool works on a keyless index. `get_answer` returns ranked, cited
evidence from the index (marked `"degraded": "no-llm-provider"`); a model
adds a written answer on top. See [Modes and
upgrades](QUICKSTART.md#modes-and-upgrades).

## Local semantic search with Ollama

Semantic search matches by meaning as well as by words. With
[Ollama](https://ollama.com) it runs on the same machine, with no key:

```bash
ollama pull embeddinggemma
repowise reindex --embedder ollama
```

`reindex` makes embedding calls only and records the embedder in
`.repowise/config.yaml`, so `repowise update` and the MCP server keep using
it. `repowise status --format json` then shows
`index_scope.search.semantic: "available"`.

To use another Ollama model, pull it and put the name in `.repowise/.env`
before `reindex`, so the MCP server reads it too:

```bash
ollama pull nomic-embed-text
echo "OLLAMA_EMBEDDING_MODEL=nomic-embed-text" >> .repowise/.env
repowise reindex --embedder ollama
```

`OLLAMA_BASE_URL` points at an Ollama server other than
`http://localhost:11434`. Ollama also serves as the documentation model:
`repowise generate --provider ollama`.

## Local semantic search without a service

The `local-embed` extra embeds inside the repowise process with
[fastembed](https://github.com/qdrant/fastembed) (ONNX, `BAAI/bge-small-en-v1.5`).
There is no server to run and no key. Both it and Ollama are supported; pick
this one when you would rather not keep Ollama running:

```bash
pip install "repowise[local-embed]"
repowise reindex --embedder fastembed
```

The extra adds about 65 MB of packages, and the model (about 65 MB) downloads
once to `~/.repowise/models` (set `FASTEMBED_CACHE_PATH` to move it). A query
embeds in a few milliseconds on a laptop CPU. `reindex` records the embedder
in `.repowise/config.yaml` like any other, so the MCP server and
`repowise update` use it; install the extra wherever they run. The extra
covers Python 3.11 and 3.12; on 3.13 and newer, run `pip install fastembed`
instead.

## Air-gapped networks

1. Install from wheels. On a connected machine with the same OS and Python,
   `python -m pip download repowise -d wheels/`; copy `wheels/` across and
   run `python -m pip install --no-index --find-links wheels/ repowise`.
2. Turn off every network path repowise owns (telemetry, update and release
   checks, plan status, agent sign-in checks, decision sync):

   ```bash
   export REPOWISE_OFFLINE=1
   ```

3. Index keyless: `repowise init --yes --no-prose`.
4. For semantic search, run `ollama pull embeddinggemma` on a connected
   machine, copy Ollama's model folder (`~/.ollama/models` for a user install,
   or the path in `OLLAMA_MODELS`) to the offline machine, then run
   `repowise reindex --embedder ollama`. With the `local-embed` extra
   instead, include it in the wheel download (`repowise[local-embed]`), copy
   `~/.repowise/models` from a machine that ran `reindex --embedder
   fastembed`, and set `HF_HUB_OFFLINE=1`.
5. For the dashboard, run `repowise serve` once on a connected machine. It
   downloads the UI to `~/.repowise/web/`; copy that folder to the same path
   on the offline machine. `repowise serve` then runs from the cached copy
   (it needs Node 20 or newer).

Every variable: [Config](../reference/CONFIG.md).

## CI

The gates (`repowise coverage check`, `doc-drift --check`, `security check`,
`health check`, `risk`) need only git: no index, database, model or key. See
[Repowise in CI](CI.md).

A job that builds an index:

```bash
REPOWISE_NO_SAVE_KEY=1 repowise init --yes --no-prose --no-editor-setup
```

`--no-editor-setup` writes only `.repowise/`: no agent config files, editor
files or git hooks. `REPOWISE_NO_SAVE_KEY` keeps a key from the job's
environment out of `.repowise/.env`.

## Benchmarks and evaluations

To run Repowise as one arm of a benchmark:

1. Use a full clone. Hotspots, ownership, co-change and change risk come from
   git history, and a shallow clone has almost none. Bound the history read
   with `--commit-limit N` (default 5000) instead.
2. Index each task repository at its pinned commit before the agent starts,
   so the timed run measures the agent, not indexing:

   ```bash
   repowise init --yes --no-prose --no-editor-setup --commit-limit 5000
   ```

3. Add local semantic search with the [Ollama steps](#local-semantic-search-with-ollama).
   `repowise status --format json` should show
   `index_scope.search.semantic: "available"`, and `search_codebase` results
   list `vector` in their `sources`.
4. Give the agent the stdio server. `repowise mcp /path/to/repo` serves that
   index; `repowise agents print-config claude-code` prints a ready
   `mcpServers` entry for the current repository.
   Pin the tool list with `--tools` (for example `--tools standard`) so every
   trial sees the same tools.
5. A model run is optional. Price it first with
   `repowise generate --provider <id> --dry-run`; a signed-in Claude Code,
   Codex or Copilot (`claude_cli`, `codex_cli`, `copilot_cli`) runs on plan
   quota.

For the benchmark's logs, `repowise status --format json` records each run's
mode and search legs (`index_scope`) and the indexed commit
(`state.last_sync_commit`).
