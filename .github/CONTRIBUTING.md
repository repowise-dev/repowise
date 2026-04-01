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
git clone https://github.com/RaghavChamadiya/repowise.git
cd repowise

# Install Python dependencies
uv sync --all-extras

# Install web frontend dependencies
npm install

# Build the web frontend
npm run build

# Run tests
pytest
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
   pytest
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

## Project Structure

```
repowise/
  packages/
    core/     # Ingestion pipeline, analysis, generation engine
    cli/      # CLI commands (click-based)
    server/   # FastAPI API + MCP server
    web/      # Next.js frontend
  tests/      # Unit and integration tests
  docs/       # Documentation
```

## Code Style

- **Python**: Formatted with [ruff](https://docs.astral.sh/ruff/) (`ruff format .`, `ruff check .`)
- **TypeScript**: Linted with ESLint (`npm run lint`)
- Keep functions small and focused
- Write docstrings for public APIs

## Testing

- Add tests for new features and bug fixes
- Place tests in `tests/unit/` or `tests/integration/`
- Run the full suite with `pytest`

## Pull Request Guidelines

- Keep PRs focused on a single change
- Write a clear description of what and why
- Reference any related issues
- Ensure CI passes before requesting review
- All PRs require at least one code owner approval

## Reporting Issues

- Use [GitHub Issues](https://github.com/RaghavChamadiya/repowise/issues) for bugs and feature requests
- For security vulnerabilities, see [SECURITY.md](SECURITY.md)

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0](LICENSE) license.
