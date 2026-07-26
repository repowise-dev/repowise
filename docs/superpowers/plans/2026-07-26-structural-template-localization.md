# Structural Template Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render all deterministic structural wiki templates in the configured output language, with complete English fallback.

**Architecture:** A dependency-free label catalog resolves the generator's normalized language into a complete mapping. The shared structural renderer passes that mapping to every template, so template changes are localized and all six deterministic page kinds share the same fallback behavior.

**Tech Stack:** Python 3.12, Jinja2, pytest, uv.

---

### Task 1: Add complete label resolution

**Files:**
- Create: `packages/core/src/repowise/core/generation/structural_labels.py`
- Test: `tests/unit/generation/test_structural_labels.py`

- [ ] **Step 1: Write failing resolver tests**

```python
from repowise.core.generation.structural_labels import resolve_structural_labels


def test_resolves_german_labels() -> None:
    assert resolve_structural_labels("de")["overview"] == "Überblick"


def test_unknown_language_uses_complete_english_catalog() -> None:
    assert resolve_structural_labels("xx")["overview"] == "Overview"
    assert resolve_structural_labels(None)["no_callers"] == "No internal callers were resolved for this file."
```

- [ ] **Step 2: Verify the tests fail**

Run: `uv run pytest tests/unit/generation/test_structural_labels.py -q`

Expected: collection fails because `structural_labels` does not exist.

- [ ] **Step 3: Implement the catalog and resolver**

```python
ENGLISH_LABELS: dict[str, str] = {...}
GERMAN_LABELS: dict[str, dict[str, str]] = {...}


def resolve_structural_labels(language: str | None) -> dict[str, str]:
    labels = ENGLISH_LABELS.copy()
    labels.update(GERMAN_LABELS.get(language or "", {}))
    return labels
```

Every key used by the six templates must exist in `ENGLISH_LABELS`; only translated German values override it.

- [ ] **Step 4: Verify the resolver tests pass**

Run: `uv run pytest tests/unit/generation/test_structural_labels.py -q`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/repowise/core/generation/structural_labels.py tests/unit/generation/test_structural_labels.py
git commit -m "feat(generation): add structural page labels"
```

### Task 2: Provide labels to all structural templates

**Files:**
- Modify: `packages/core/src/repowise/core/generation/page_generator/structural.py:302-323`
- Modify: `packages/core/src/repowise/core/generation/templates/file_page.j2`
- Modify: `packages/core/src/repowise/core/generation/templates/symbol_spotlight.j2`
- Modify: `packages/core/src/repowise/core/generation/templates/layer_page.j2`
- Modify: `packages/core/src/repowise/core/generation/templates/infra_page.j2`
- Modify: `packages/core/src/repowise/core/generation/templates/scc_page.j2`
- Modify: `packages/core/src/repowise/core/generation/templates/api_contract.j2`
- Test: `tests/unit/generation/test_deterministic_templates.py`

- [ ] **Step 1: Add failing German rendering assertions**

Extend the existing generator fixture with German `GenerationConfig` and render each of the six structural page kinds. Assert representative headings are German (`## Überblick`, `## Öffentliche API`, `## Wo es verwendet wird`, `## Architektur`, `## Deklarierte Ziele`, `## Dateien im Zyklus`, `## Operationen`) and preserve a path/symbol assertion.

- [ ] **Step 2: Verify the new assertions fail**

Run: `uv run pytest tests/unit/generation/test_deterministic_templates.py -q`

Expected: FAIL because deterministic pages still render English headings.

- [ ] **Step 3: Inject resolved labels at the shared renderer**

```python
from repowise.core.generation.structural_labels import resolve_structural_labels

content = self._render(
    template,
    style_prefix=False,
    labels=resolve_structural_labels(self._language),
    **render_kwargs,
)
```

- [ ] **Step 4: Replace fixed template text with label lookups**

Use `{{ labels.overview }}`-style lookups for every fixed heading, metadata name, empty state, and static sentence fragment. Keep all context-derived values and code blocks unchanged.

- [ ] **Step 5: Verify structural rendering**

Run: `uv run pytest tests/unit/generation/test_deterministic_templates.py -q`

Expected: all tests pass, including German assertions.

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/repowise/core/generation/page_generator/structural.py packages/core/src/repowise/core/generation/templates tests/unit/generation/test_deterministic_templates.py
git commit -m "fix(generation): localize structural wiki pages"
```

### Task 3: Verify fallback and integration safety

**Files:**
- Modify: `tests/unit/generation/test_deterministic_templates.py`

- [ ] **Step 1: Add unknown-language fallback regression**

Render a structural page after setting the generator language to an unknown code and assert its heading and fixed empty-state text are exactly English.

- [ ] **Step 2: Verify the fallback regression passes**

Run: `uv run pytest tests/unit/generation/test_structural_labels.py tests/unit/generation/test_deterministic_templates.py -q`

Expected: all tests pass.

- [ ] **Step 3: Run targeted structural tests and lint**

Run: `uv run pytest tests/unit/generation/test_deterministic_templates.py tests/unit/generation/test_page_generator.py tests/integration/test_deterministic_generation.py -q && uv run ruff check packages/core/src/repowise/core/generation/structural_labels.py packages/core/src/repowise/core/generation/page_generator/structural.py tests/unit/generation/test_structural_labels.py tests/unit/generation/test_deterministic_templates.py`

Expected: all selected tests and lint pass.

- [ ] **Step 4: Check diff hygiene and commit**

```bash
git diff origin/main --check
git add tests/unit/generation/test_deterministic_templates.py
git commit -m "test(generation): cover structural language fallback"
```

