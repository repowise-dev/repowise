---
layout: default
title: Contributing
nav_order: 11
---

# Contributing

Contributions are welcome. Full guidelines are maintained in the repository.

**[Read CONTRIBUTING.md on GitHub →](https://github.com/repowise-dev/repowise/blob/main/CONTRIBUTING.md)**

## Quick links

- [Open an issue](https://github.com/repowise-dev/repowise/issues) — bugs, feature requests, questions
- [Open a pull request](https://github.com/repowise-dev/repowise/pulls)
- [Read the architecture guide](https://github.com/repowise-dev/repowise/blob/main/docs/ARCHITECTURE.md) — required reading before contributing

## Development setup

```bash
git clone https://github.com/repowise-dev/repowise.git
cd repowise
uv sync --all-extras     # Python dependencies
npm install              # Web frontend
npm run build            # Build frontend
pytest                   # Run tests
ruff check packages/     # Lint
```

## Security issues

For security vulnerabilities, see [SECURITY.md](https://github.com/repowise-dev/repowise/blob/main/SECURITY.md) — do not open a public issue.
