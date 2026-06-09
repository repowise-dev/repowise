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

# Install Python dependencies (uv workspace — installs all packages)
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

## Development Workflow

1. **Fork** the repository
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```
3. **Make your changes** — keep commits focused and well-described
4. **Run tests** before pushing:
   ```bash
   uv run pytest tests/unit/
   npm run lint
   npm run type-check
   ```
5. **Push** to your fork and open a **Pull Request** against `main`

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

2. **Register** in `registry.py` — add to `_BUILTIN_PROVIDERS` and the `_missing` package map

3. **Wire up configuration** in these files:
   - `rate_limiter.py` — add `RateLimitConfig` to `PROVIDER_DEFAULTS`
   - `provider_config.py` — add entry to `PROVIDER_CATALOG`
   - `provider_selection.py` — add to `_PROVIDER_DEFAULTS`, `_PROVIDER_ENV`, `_PROVIDER_SIGNUP`, and detection
   - `helpers.py` — add validation in `validate_provider_config()`

4. **Update the web UI** — add to `PROVIDERS`, `MODEL_PLACEHOLDERS`, and `PROVIDER_ENV_VARS` in `provider-section.tsx` and `run-config-form.tsx`

5. **Add tests** in `tests/unit/test_providers/` — mock the subprocess, test success/error/timeout paths (see `test_codex_cli_provider.py` for the pattern)

6. **Write docs** — `docs/<NAME>.md` and `website/<name>.md`, following `docs/CODEX.md` and `docs/OPENCODE.md`.

Adding a new language or LLM provider has a dedicated recipe — see
[docs/LANGUAGE_SUPPORT.md](../docs/LANGUAGE_SUPPORT.md).

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
