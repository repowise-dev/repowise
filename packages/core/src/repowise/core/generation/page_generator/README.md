# `page_generator`

Converts assembled context dataclasses into `GeneratedPage` objects by
rendering Jinja prompts, calling an LLM provider, and (for the long tail of
large repos) rendering deterministic template-only pages.

## Purpose

`PageGenerator.generate_all()` is the top of the documentation-generation
stage. It selects which pages to produce (via `generation.selection`), then
generates them in ordered, concurrency-bounded levels:

| Level | Page type | Notes |
| --- | --- | --- |
| 0/1 | `api_contract`, `symbol_spotlight` | merged batch |
| 2 | `file_page` | topo-ordered; tier-1 LLM or tier-2 template |
| 3 | `scc_page` | circular-dependency cycles |
| 4 | `module_page` | community / directory groups |
| 6/7/8 | `repo_overview` + `architecture_diagram`, `infra_page`, `onboarding` | merged batch |

### Tiered doc generation

`GenerationConfig.tier1_top_n` caps how many file pages get full LLM
generation. The top *N* selected files by PageRank are **tier-1** (the
existing LLM path); the rest are **tier-2** — rendered from
`templates/file_page_tier2.j2` with no LLM call and embedded for search.
`tier1_top_n = None` (default) puts every selected page in tier-1, so
behaviour is unchanged unless the knob is set.

## Public API

```python
from repowise.core.generation.page_generator import PageGenerator, SYSTEM_PROMPTS, PriorPage
```

- `PageGenerator(provider, assembler, config, ...)` — `await .generate_all(...)`
  returns `list[GeneratedPage]`. Per-type `generate_file_page` / `generate_*`
  methods are also public.
- `SYSTEM_PROMPTS` — constant system prompt per page type (prefix-cacheable).
- `PriorPage` — cross-run reuse snapshot keyed by `page_id`.

## Internal layout

- `core.py` — `PageGenerator` (provider call, two-tier caching, file-page LLM
  path + deterministic tier-2 renderer).
- `pertype.py` — `PerTypeGenerationMixin` with the per-page-type methods.
- `orchestrate.py` — `_GenerationRun` (per-call state, level runner) and
  `run_generate_all` (the entry point `generate_all` delegates to).
- `levels.py` — pure per-level coroutine builders reading the run state.
- `tiering.py` — `partition_file_tiers` (tier-1/tier-2 split by PageRank).
- `prompts.py`, `validation.py`, `helpers.py` — constants and pure helpers.

## Extension points

- New page type: add a `generate_*` method to `pertype.py`, a level builder to
  `levels.py`, and wire it into `_GenerationRun.execute()`.
- New tier policy: extend `partition_file_tiers` / add a renderer template.

## Tests

- `tests/unit/generation/test_page_generator.py`
- `tests/unit/generation/test_page_generator_tiering.py`
- `tests/integration/test_generation_pipeline.py`
"""
