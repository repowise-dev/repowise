# Changelog

All notable changes to repowise will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Use `git-cliff` to auto-generate entries from conventional commits -->

---

## [Unreleased]

### Added
- Monorepo scaffold: uv workspace with `packages/core`, `packages/cli`, `packages/server`, `packages/web`
- Provider abstraction layer: `BaseProvider`, `GeneratedResponse`, `ProviderError`, `RateLimitError`
- `AnthropicProvider` with prompt caching support
- `OpenAIProvider` with OpenAI Chat Completions API
- `OllamaProvider` for local offline inference (OpenAI-compatible endpoint)
- `LiteLLMProvider` for 100+ models via LiteLLM proxy
- `MockProvider` for testing without API keys
- `RateLimiter`: async sliding-window RPM + TPM limits with exponential backoff
- `ProviderRegistry`: dynamic provider loading with custom provider registration
- CI pipeline: GitHub Actions matrix on Python 3.11, 3.12, 3.13
- Pre-commit hooks: ruff lint + format, mypy, standard file checks
- **Folder exclusion** — three-layer system for skipping paths during ingestion:
  - `FileTraverser(extra_exclude_patterns=[...])` — pass gitignore-style patterns at construction time; applied to both directory pruning and file-level filtering
  - Per-directory `.repowiseIgnore` — traverser loads one from each visited directory (like git's per-directory `.gitignore`); patterns are relative to that directory and cached for efficiency
  - `repowise init --exclude/-x PATTERN` — repeatable CLI flag; patterns are merged with `exclude_patterns` from `config.yaml` and persisted back to `.repowise/config.yaml`
  - `repowise update` reads `exclude_patterns` from `config.yaml` automatically
  - Web UI **Excluded Paths** section on `/repos/[id]/settings`: chip editor, Enter-to-add input, six quick-add suggestions (`vendor/`, `dist/`, `build/`, `node_modules/`, `*.generated.*`, `**/fixtures/**`), empty-state message, gitignore-syntax tooltip; saved via `PATCH /api/repos/{id}` as `settings.exclude_patterns`
  - `helpers.save_config()` now round-trips `config.yaml` to preserve all existing keys when updating provider/model/embedder; accepts optional `exclude_patterns` keyword argument
  - `scheduler.py` logs `repo.settings.exclude_patterns` in polling fallback as preparation for future full-sync wiring
- 13 new unit tests in `tests/unit/ingestion/test_traverser.py` covering `extra_exclude_patterns` and per-directory `.repowiseIgnore` behaviour

---

[Unreleased]: https://github.com/repowise-ai/repowise/compare/HEAD
