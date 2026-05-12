## Plan Complete: OpenAI-Compatible Embedding Endpoint

Added an `openai_compatible` embedder so users can point repowise at any OpenAI-compatible embedding API (Ollama, LM Studio, Azure OpenAI, Mistral, etc.) by configuring a custom base URL. The base URL is automatically saved to `~/.repowise/config.yaml` and restored on subsequent runs.

**Phases Completed:** 2 of 2
1. ✅ Phase 1: Core `OpenAICompatibleEmbedder` provider
2. ✅ Phase 2: CLI `serve` / `init` / `reindex` / `ui` wiring

**All Files Created/Modified:**
- `packages/core/src/repowise/core/providers/embedding/openai_compatible.py` *(new)*
- `packages/core/src/repowise/core/providers/embedding/registry.py`
- `packages/core/src/repowise/core/providers/embedding/__init__.py`
- `packages/cli/src/repowise/cli/commands/serve_cmd.py`
- `packages/cli/src/repowise/cli/commands/init_cmd.py`
- `packages/cli/src/repowise/cli/commands/reindex_cmd.py`
- `packages/cli/src/repowise/cli/ui.py`
- `tests/unit/test_providers/test_openai_compatible_embedder.py` *(new)*
- `tests/unit/cli/test_openai_compatible_cli.py` *(new)*

**Key Functions/Classes Added:**
- `OpenAICompatibleEmbedder` — subclass of `OpenAIEmbedder`; resolves `base_url` from `OPENAI_COMPATIBLE_BASE_URL` → `OPENAI_BASE_URL` → none; supports keyless local servers
- `_setup_embedder()` in serve_cmd — updated to restore `embedder_base_url` and prompt for base URL when selecting `openai_compatible`
- `_save_global_embedder()` — updated to persist `embedder_base_url`
- `_resolve_embedder()` in init_cmd — detects `OPENAI_COMPATIBLE_BASE_URL` / `OPENAI_BASE_URL`
- `_resolve_embedder_from_env()` in ui.py — same detection logic for advanced config

**Test Coverage:**
- Total tests written: 20 (11 provider + 9 CLI)
- All tests passing: ✅

**Recommendations for Next Steps:**
- Add `openrouter` to the advanced-config UI embedder choices (`ui.py`) for full parity with the serve command
- Consider surfacing `openai_compatible` in the web dashboard embedder settings if one exists
