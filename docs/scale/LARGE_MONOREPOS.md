# Indexing a large monorepo

repowise indexes a multi-million-line monorepo on a single machine, with its
full git history. This page gives the exact commands for a repository of about
4 million lines and 20,000+ commits in C, C++, Python and Scala, from the first
clone to keeping the index current.

## What to expect

Measured end to end, keyless (`repowise init --no-prose`), 8 CPUs, 2 parse
workers:

| Repository | Tracked files | Lines of code | First index | Peak memory | Index on disk |
|---|---:|---:|---:|---:|---:|
| elastic/elasticsearch | 34,315 | 4.0M | 52 min | 7.8 GiB | 4.2 GiB |
| dotnet/runtime | 58,924 | 15.0M | 120 min | 11.7 GiB | 7.7 GiB |
| ClickHouse/ClickHouse | 93,604 | 4.29M | 98 min | 25.0 GiB | 2.6 GiB |

ClickHouse ran on a full clone with all 321,097 commits: 62,167 files indexed
(C++, C, Python, SQL, shell), 249,188 symbols, a 742,968-edge graph. About
15 GiB of its peak is a one-time `git commit-graph write` over that history,
running beside the graph build. With PostgreSQL as the store the first index
takes 94 min, with a 1.9 GiB database. MCP answers on the finished index,
p50: `get_context` 0.24 s, `get_symbol` 0.29 s, `search_codebase` 2.3 s,
`get_answer` 6.3 s (PostgreSQL: 0.16, 0.09, 1.2 and 5.9 s).

More rows and the method: [BENCHMARKS.md](../BENCHMARKS.md#scale).

A machine with 8 vCPUs, 32 GB of RAM and 50 GB of free disk runs a 4M-line
index with room to spare. The first index is a one-time cost; after it,
`repowise update` re-analyses only what each commit changed.

## 1. Prepare the checkout

Index a normal clone with its history:

```bash
git clone https://gitlab.example.com/group/monorepo.git
cd monorepo
git rev-list --count HEAD        # how many commits you have
```

Use a full clone and choose how much history to read with
`--commit-limit` (below), not by trimming the clone. A shallow clone
(`--depth` or `--shallow-since`) leaves cut-off commits at its boundary,
and git reports each of them as adding every file in the repository. On a
merge-heavy repository that is thousands of commits: a `--shallow-since`
clone of ClickHouse kept 9,715 of them, each a 6 s full-tree diff, which
added about an hour to the history pass. If you already have a shallow
clone, run `git fetch --unshallow` before the first index.

**C and C++ include paths.** repowise resolves `#include` lines through
`compile_commands.json` when it finds one at the repo root or in `build/`,
alongside the CMake and Bazel target files it reads on its own. A CMake
configure step is enough to write it; nothing has to compile:

```bash
cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

For Bazel, generate it with a compile-commands extractor (for example
`hedron_compile_commands`) and leave the file at the repo root.

Python and Scala need no setup: Python imports resolve from the tree, and
Scala resolves through the shared JVM index, reading SBT and Mill build files
where it needs them.

**Exclusions.** Leave out what nobody edits: vendored libraries, generated
code, large test fixtures. Put the patterns in `.repowiseIgnore` at the root
(gitignore syntax, read on every run) or pass them once with `-x`:

```bash
cat > .repowiseIgnore <<'EOF'
third_party/
contrib/
**/generated/
tests/fixtures/data/
EOF
```

Git submodules are skipped by default; `--include-submodules` indexes them.

## 2. Choose the database

| | SQLite (default) | PostgreSQL |
|---|---|---|
| Best for | One machine, one user | A shared server, the web UI and MCP for a team |
| Setup | Nothing: `.repowise/` in the repo | PostgreSQL with the `pgvector` extension available |

For PostgreSQL:

```bash
pip install "repowise[postgres]"
export REPOWISE_DB_URL="postgresql+asyncpg://repowise:secret@db.internal:5432/repowise"
```

The schema is created on first use. After the first index on PostgreSQL, run
`ANALYZE;` once in that database so the query planner sees the new table
sizes.

## 3. First index

```bash
REPOWISE_PARSE_WORKERS=4 repowise init --yes --no-prose --no-editor-setup \
  --commit-limit 25000
```

| Flag | Why |
|---|---|
| `--no-prose` | Builds every page from code, with no model and no key. Add the model-written concept pages later with `repowise generate`. |
| `--commit-limit 25000` | Reads the newest 25,000 commits for file history, ownership, co-change and the commit list. Set it above `git rev-list --count HEAD` to read the full history. Saved to config. |
| `REPOWISE_PARSE_WORKERS=4` | Parse processes. Each holds its own parser, so this is the main memory lever; the default is the CPU count, at most 8. |
| `--no-editor-setup` | On a server, writes nothing outside `.repowise/`. Drop it on a developer machine to wire up editors and agents. |
| `--yes` | Takes every default without asking. |

For the fastest first pass, index the graph and essential history first and
add the rest later, with no model calls either way:

```bash
repowise init --yes --no-prose --mode fast
repowise update --full
```

An interrupted first index continues where it stopped with
`repowise init --resume`.

### Full history

`commit_limit` sets how far back churn, ownership, co-change and the commit
list see; change records cover the newest 10,000. The default is the newest 5,000 commits; there is no
upper bound, and depth is cheap. On git/git (82,925 commits, 61,453 of them
non-merge), the full history costs a few seconds and a few hundred MB:

| `--commit-limit` | Commits read | First index | Peak memory | Index on disk |
|---:|---:|---:|---:|---:|
| 5,000 (default) | 5,000 | 5m 09s | 1.47 GiB | 361 MB |
| 100,000 (full) | 61,453 | 5m 12s | 1.67 GiB | 466 MB |

To deepen an existing index without re-indexing:

```bash
repowise config set commit_limit 25000
repowise update --index-only --defer-docs --refresh-git
```

### Files left out

Parsed source files over 2 MB, minified bundles, files with no parser over
500 KB, and generated files are left out of the index. The console names the
first few source files; every one of them, with its size and reason, is in
`.repowise/skipped_files.txt`, rewritten on each `init` and `update`:

```text
# Files repowise did not index: path, size, reason (tab-separated)
src/parser/grammar_tables.cpp	2,310 KB	over the 2,048 KB limit
tests/data/fixtures.tsv	48,112 KB	over the size limit for files without a parser
```

## 4. Keep it current

`repowise update` diffs the new commit against the last indexed one and
re-analyses only what changed. Pick a trigger
([AUTO_SYNC.md](AUTO_SYNC.md) has the details):

| Setup | Trigger |
|---|---|
| Developer machine | The post-commit hook `init` installs (`repowise hook install`) |
| Shared server on GitLab | The GitLab push webhook, with the 15-minute poller as a safety net |

A change to a build file the graph reads (`CMakeLists.txt`, `BUILD`,
`BUILD.bazel`) rebuilds the whole graph on that update, because include and
target resolution depend on it. Source-only commits patch the graph in place.

## Memory and time levers

In order of effect:

```bash
REPOWISE_PARSE_WORKERS=2 repowise init ...   # fewer parse processes, less memory
repowise init --mode fast ...                # index only; `repowise update --full` adds the rest
repowise init -x 'third_party/' ...          # exclude what you never edit
repowise init --max-file-pages 2000 ...      # cap file pages, highest importance first
```

After the first index, `repowise config list --suggested` reads what it
stored and names the settings that fit this repository, each with its numbers
and the command: a deeper `commit_limit` when many files have no commit in the
window, `exclude_patterns` for directories holding only vendored or generated
code, and every file page when the automatic cap left production files out.

Related: [WORKSPACES.md](WORKSPACES.md) indexes the monorepo together with its
smaller sibling repositories behind one MCP server.
