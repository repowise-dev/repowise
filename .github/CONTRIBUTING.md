# Contributing to Repowise

Thanks for your interest in contributing to Repowise! This guide will help you get started.

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Git

### Local Setup

```bash
# Clone the repo
git clone https://github.com/repowise-dev/repowise.git
cd repowise

# Install Python dependencies (uv workspace, installs all packages)
uv sync --all-packages

# Install web frontend dependencies
npm install

# Build the web frontend
npm run build

# Verify the CLI runs
uv run repowise --version

# Run tests (both suites; CI runs these two on every pull request)
uv run pytest tests/providers/ tests/unit/
```

## Getting oriented

This is a ~3,000 file codebase, and reading it front to back is not the plan. Repowise
exists to make that unnecessary, so use it on itself.

### Without installing anything

We keep a public, always-fresh index of this repository at
**[repowise.dev/repo/repowise-dev/repowise](https://repowise.dev/repo/repowise-dev/repowise)**.
It re-indexes on every push. Pick the tab that matches your question:

| Your question | Where to look |
|---|---|
| What are the moving parts? | [Overview](https://repowise.dev/repo/repowise-dev/repowise/overview) and [Architecture](https://repowise.dev/repo/repowise-dev/repowise/architecture) |
| Where does this symbol live? | [Files](https://repowise.dev/repo/repowise-dev/repowise/files) |
| Which files are dangerous to touch? | [Code Health](https://repowise.dev/repo/repowise-dev/repowise/code-health), and the hotspot table on the landing page |
| Who knows this area? | [People & History](https://repowise.dev/repo/repowise-dev/repowise/owners) |
| Why is it built this way? | [Decisions](https://repowise.dev/repo/repowise-dev/repowise/decisions) |
| What changed recently, and how risky was it? | [Commits](https://repowise.dev/repo/repowise-dev/repowise/commits) |

### Locally, with your agent

Better still, index this repo with the tool you are contributing to. It is free, needs
no API key, and takes a couple of minutes:

```bash
uv run repowise init --no-prose -y   # graph, git history, health, decisions. No LLM, no spend.
uv run repowise serve                # dashboard + MCP server on localhost
```

Then point your coding agent at the MCP server (see the
[Start in minutes](../README.md#quickstart) for Claude Code, Codex
and others) and ask it questions directly:

```
get_context for packages/core/src/repowise/core/pipeline/orchestrator.py
get_why "why is doc generation split from ingestion?"
```

If something about this experience is bad, that is a bug worth reporting. Contributors
are the only people who use repowise on repowise with fresh eyes.

### Then read

- [docs/architecture/](../docs/architecture/README.md) for the written architecture
- [docs/layers/INTELLIGENCE_LAYERS.md](../docs/layers/INTELLIGENCE_LAYERS.md) for what
  each of the five layers computes and where its code lives
- [docs/reference/CLI_REFERENCE.md](../docs/reference/CLI_REFERENCE.md) for every
  command and flag

## Looking for something to work on

- **[Good first issues](https://github.com/repowise-dev/repowise/labels/good%20first%20issue)**.
  **These are reserved for people making their first contributions here.** They are the
  only route new contributors have into the codebase, and they disappear within hours if
  everyone takes them, so if you have already had work merged please leave them. It costs
  you very little and it is the difference between this project having new contributors
  and not.
- **[Help wanted](https://github.com/repowise-dev/repowise/labels/help%20wanted)** is the
  opposite and is where experienced contributors should be. It marks work that is scoped
  and ready but needs someone who already knows their way around. There is always more of
  it than there are people, so take as much as you want.
- **[The refactoring backlog](https://repowise.dev/repo/repowise-dev/repowise/refactoring).**
  Repowise ranks its own concrete refactoring plans (Extract Class, Split File, Break
  Cycle, and so on) with the blast radius attached. Each card has a copy-to-agent
  button. Picking one off that list is a genuinely useful contribution, and it is the
  fastest way to learn how the health layer thinks.
- **Language support.** A new language is five small steps: a `LanguageSpec`, a tag,
  a `.scm` query file, a parser config and the grammar dependency, with no changes to
  the parser core. Optional extractors and call-resolution seams add depth on top.
  Recipe: [docs/architecture/language-support.md](../docs/architecture/language-support.md).
  Current coverage: [docs/layers/LANGUAGE_SUPPORT.md](../docs/layers/LANGUAGE_SUPPORT.md).

### Claiming an issue

Issues are assigned to one person at a time, so that two contributors do not build the
same fix in parallel and one of them has to throw the work away.

- Comment on the issue saying you are taking it. **That comment is what reserves the
  issue, from the moment you post it.** A maintainer will assign it to you when they next
  go through the tracker, which may be a few days later; the issue is yours in the
  meantime and does not become available again because the assignee field is still empty.
- **Before you start, read the thread.** If somebody else has already said they are taking
  it, it is theirs, even if nothing is assigned and no code has appeared yet. Somebody who
  posted a plan and then watched a finished PR land the same evening does not come back,
  and that costs this project more than any single fix is worth.
- Only the person who claimed it should open a PR for that issue. A PR opened over
  somebody else's claim will be held, not merged, however good it is.
- If you get pulled away, a one-line comment to unclaim is enough. It carries no
  obligation and no hard feelings, and it frees the issue for someone else.
- A claim expires after two weeks of silence, whether or not it was ever assigned,
  and the issue goes back to open. Claiming is not a way to park an issue.

Questions about scope before you claim are welcome. Asking is not claiming.

Some issues describe several separable pieces of work. Say which piece you are taking,
and it can be split into its own issue so more than one person can work in parallel.

Before you start, check the file you are about to edit:

```bash
uv run repowise health --file <path>   # score, markers, findings
uv run repowise risk HEAD              # or ask get_risk from your agent
```

Some files in this repo are bug magnets: high churn, a long run of prior fixes, often a
bus factor of one. The
[hotspot table](https://repowise.dev/repo/repowise-dev/repowise/code-health) names the
current ones. Changes there are welcome, but expect closer review and bring tests.

## Development Workflow

1. **Fork** the repository
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```
3. **Make your changes**: keep commits focused and well-described
4. **Run tests** before pushing:
   ```bash
   uv run pytest tests/providers/ tests/unit/   # both, CI runs both
   npm run lint
   npm run type-check
   ```
   `tests/providers/` is easy to forget and CI does not forget it, so a run that
   skips it is the most common way a green local suite turns red on the PR.
5. **Check your own change** with the tool you are contributing to:
   ```bash
   uv run repowise risk main..HEAD        # 0-10 defect score, plus may_break,
                                          # missing_cochanges and missing_tests
   uv run repowise impacted-tests --staged  # the tests your diff actually exercises
   uv run repowise health --file <path>   # did the file you touched get worse?
   ```
   None of this is a gate, and none of it calls an LLM. It is the same signal the
   reviewer will be looking at, and running it yourself catches the boring problems
   (a forgotten companion file, an untested hotspot) before anyone else has to.
6. **Push** to your fork and open a **Pull Request** against `main`

## Branch Naming

Use descriptive prefixes:

| Prefix | Purpose |
|--------|---------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `chore/` | Maintenance, CI, docs |
| `refactor/` | Code restructuring |

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/) with an
optional scope, e.g. `feat(cli): add --resume to init` or `fix(health): bound
duplication detection`. Keep the subject line in the imperative mood and under
~72 characters.

## Project Structure

```
repowise/
  packages/
    core/        # Ingestion pipeline, analysis, generation engine
    cli/         # CLI commands (click-based)
    server/      # FastAPI API + MCP server
    types/       # Shared TypeScript types
    api-client/  # Typed fetch client over the server API
    ui/          # Shared React UI components
    web/         # Next.js frontend
    vscode/      # VS Code extension (has its own CI job)
  tests/         # providers/, unit/ and integration/
  docs/          # Documentation
```

## Code Style

- **Python**: Linted with [ruff](https://docs.astral.sh/ruff/). CI runs `ruff check .`
  and nothing else.
- **Do not run `ruff format .`** The repository is not kept under the ruff formatter,
  nothing in CI checks formatting, and the dependency pin is wide enough that a fresh
  install can pick up a newer ruff whose formatter rewrites hundreds of files. That diff
  buries the change you actually made.
- **TypeScript**: Linted with ESLint (`npm run lint`) and type-checked (`npm run type-check`)
- Keep functions small and focused
- Write docstrings for public APIs

### Adding a new LLM provider

1. Create `packages/core/src/repowise/core/providers/llm/<name>.py` with a
   `BaseProvider` subclass. For an OpenAI-compatible API, subclass
   `OpenAICompatibleProvider` from `openai_compat.py`, as `deepseek.py` does.
2. Add one `ProviderSpec` to `_SPECS` in
   `packages/core/src/repowise/core/providers/llm/specs.py`: key and base-URL env vars,
   default model and model list, rate limit, package, picker rank and signup URL.
   The registry tables, the server catalog, the init picker and the web UI all read
   it, so there is no second list to edit.
3. Add tests in `tests/unit/test_providers/`. If the spec has a `picker_rank`, add
   the name to the frozen picker order in `tests/unit/cli/test_init_ux.py`.

An agent CLI used as a backend (`claude_cli`, `codex_cli`, `opencode`) follows the
agent platform recipe instead.

Adding a new language has a dedicated recipe, see
[docs/architecture/language-support.md](../docs/architecture/language-support.md).
Adding an agent integration or an agent CLI indexing backend has its own recipe, see
[docs/architecture/agent-platform.md](../docs/architecture/agent-platform.md).

## Testing

- Add tests for new features and bug fixes
- Place tests in `tests/unit/` or `tests/integration/`
- Run the full suite with `uv run pytest`
- A test asserting on a `caplog` record needs `caplog.set_level(logging.INFO, logger="<the module's full logger name>")` (or the level you're asserting on) — setting the root logger's level is not enough when an ancestor logger (e.g. `repowise.core`, `repowise.server`) has its own level raised, since Python resolves the *effective* level from the nearest ancestor that has one set, not from root.

### Retrieval guard

`tests/unit/server/mcp/test_retrieval_guard.py` indexes `tests/fixtures/sample_repo`
with no API key and asks `search_codebase` (default and `limit=10`) and
`get_answer` the corpus questions in
`tests/fixtures/mcp/retrieval_guard_corpus.json`, whose gold files were read off
the code by hand. `get_answer` is measured twice: with no provider (the degraded
retrieval-only shape) and with a stub provider whose fixed answer cites the top
retrieved file (the synthesised shape; it measures projection, not answer
quality). Each arm reports coverage at 1, 5 and all served files, file
precision, median response tokens and, for information only, median files
served, compared against `tests/fixtures/mcp/retrieval_guard_baseline.json`.
It runs inside `tests/unit/` in seconds, so every pull request runs it, and CI
writes the metrics table to the job summary. Run it alone with
`uv run pytest tests/unit/server/mcp/test_retrieval_guard.py -s` to see the
table. The test clears `MAX_MCP_OUTPUT_TOKENS` and every `REPOWISE_*` variable,
so local settings do not change the result.

It fails when coverage or precision drops by more than 0.03 or median tokens
grow by more than 10%. An improvement passes with a note (shown with `-s` and in
the CI step summary), but it is only locked in once you commit the refreshed
baseline. If your change moves ranking or response shape on purpose, or improves
it, refresh the baseline in the same pull request:

```bash
REPOWISE_UPDATE_RETRIEVAL_BASELINE=1 uv run pytest tests/unit/server/mcp/test_retrieval_guard.py -s
```

and paste the before/after table into the description. To see which questions
moved, run with `REPOWISE_RETRIEVAL_GUARD_DUMP=<file>` on both branches and diff
the two dumps (do not commit them). Always report coverage
with precision beside it: serving more files raises coverage for free, so a
coverage gain that costs precision is a trade-off to justify, not a win.

## Pull Request Guidelines

- Keep PRs focused on a single change
- Write a clear description of what and why
- Reference any related issues
- Ensure CI passes before requesting review
- All PRs require at least one code owner approval

## Reporting Issues

- Use [GitHub Issues](https://github.com/repowise-dev/repowise/issues) for bugs and feature requests
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)
- For questions and discussion, join us on [Discord](https://discord.gg/cQVpuDB6rh)

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0](../LICENSE) license.
