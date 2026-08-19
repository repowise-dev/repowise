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

# Run tests
uv run pytest tests/unit/
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
[Quickstart](../README.md#quickstart-under-5-minutes-no-api-key) for Claude Code, Codex
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

- **[Good first issues](https://github.com/repowise-dev/repowise/labels/good%20first%20issue)** on the tracker.
- **[Help wanted](https://github.com/repowise-dev/repowise/labels/help%20wanted)** for
  issues that are scoped and ready to pick up but need more context than a first issue.
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

- Comment on the issue saying you are taking it, and a maintainer will assign it to you.
- Only the assignee should open a PR for that issue.
- If you get pulled away, a one-line comment to unclaim is enough. It carries no
  obligation and no hard feelings, and it frees the issue for someone else.
- An assigned issue that goes quiet for two weeks goes back to unassigned.

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
   uv run pytest tests/unit/
   npm run lint
   npm run type-check
   ```
5. **Check your own change** with the tool you are contributing to:
   ```bash
   uv run repowise risk main..HEAD        # 0-10 defect score, plus will_break,
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
    core/     # Ingestion pipeline, analysis, generation engine
    cli/      # CLI commands (click-based)
    server/   # FastAPI API + MCP server
    types/    # Shared TypeScript types
    ui/       # Shared React UI components
    web/      # Next.js frontend
  tests/      # Unit and integration tests
  docs/       # Documentation
```

## Code Style

- **Python**: Formatted with [ruff](https://docs.astral.sh/ruff/) (`ruff format .`, `ruff check .`)
- **TypeScript**: Linted with ESLint (`npm run lint`) and type-checked (`npm run type-check`)
- Keep functions small and focused
- Write docstrings for public APIs

### Adding a new LLM provider

1. **Create `packages/core/src/repowise/core/providers/llm/<name>.py`**
   - Subclass `BaseProvider` and implement `generate()`, `provider_name`, `model_name`
   - For local CLI providers, use `asyncio.create_subprocess_exec` (never `shell=True`), validate user-supplied model names against a safe character set, and resolve paths with `Path.resolve()`
   - See `opencode.py` for a clean reference implementation

2. **Register** in `registry.py`: add to `_BUILTIN_PROVIDERS` and the `_missing` package map

3. **Wire up configuration** in these files:
   - `rate_limiter.py`, add `RateLimitConfig` to `PROVIDER_DEFAULTS`
   - `provider_config.py`, add entry to `PROVIDER_CATALOG`
   - `provider_selection.py`, add to `_PROVIDER_DEFAULTS`, `_PROVIDER_ENV`, `_PROVIDER_SIGNUP`, and detection
   - `helpers.py`, add validation in `validate_provider_config()`

4. **Update the web UI**: add to `PROVIDERS`, `MODEL_PLACEHOLDERS`, and `PROVIDER_ENV_VARS` in `provider-section.tsx` and `run-config-form.tsx`

5. **Add tests** in `tests/unit/test_providers/`: mock the subprocess, test success/error/timeout paths (see `test_codex_cli_provider.py` for the pattern)

6. **Write docs**: `docs/<NAME>.md` and `website/<name>.md`, following `docs/agent/CODEX.md` and `docs/agent/OPENCODE.md`.

Adding a new language has a dedicated recipe, see
[docs/architecture/language-support.md](../docs/architecture/language-support.md).

## Testing

- Add tests for new features and bug fixes
- Place tests in `tests/unit/` or `tests/integration/`
- Run the full suite with `uv run pytest`

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
