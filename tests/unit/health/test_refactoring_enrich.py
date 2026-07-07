"""Unit tests for the opt-in LLM refactoring-enrichment engine.

Uses ``MockProvider`` so no API calls happen. Covers: per-type source
gathering, prompt assembly, on-disk caching, diff extraction, the Extract
Class LCOM4 self-check, and the config gate.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.refactoring.llm import (
    EnrichmentResult,
    enrich_suggestion,
    llm_enrichment_enabled,
)
from repowise.core.analysis.health.refactoring.llm.enrich import (
    _MAX_IMPORT_HEAD_LINES,
    SourceSpan,
    _build_user_prompt,
    _extract_diff,
    _gather_spans,
    _validate_extract_method,
)
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.core.providers.llm.mock import MockProvider


def _extract_class_suggestion() -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_class",
        file_path="pkg/example.py",
        target_symbol="GodClass",
        line_start=1,
        line_end=20,
        plan={
            "groups": [
                {"name": None, "methods": ["get"], "fields": ["x"]},
                {"name": None, "methods": ["put"], "fields": ["y"]},
            ]
        },
        evidence={"lcom4": 2, "method_count": 4, "field_count": 2, "wmc": 12},
        impact_delta=2.5,
        effort_bucket="L",
        blast_radius={"dependents_count": 0},
        confidence="high",
        source_biomarker="low_cohesion",
    )


def _split_file_suggestion() -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="split_file",
        file_path="pkg/big.py",
        target_symbol="big.py -> 2 files",
        line_start=None,
        line_end=None,
        plan={
            "groups": [
                {
                    "name": "parsing",
                    "symbols": ["parse_a", "parse_b"],
                    "suggested_file": "pkg/parsing.py",
                },
                {
                    "name": "rendering",
                    "symbols": ["render_a", "render_b"],
                    "suggested_file": "pkg/rendering.py",
                },
            ],
            "residual": {"symbols": ["shared_helper"]},
            "shim_required": True,
        },
        evidence={"file_nloc": 400, "symbol_count": 8, "group_count": 2, "modularity": 0.5},
        impact_delta=0.0,
        effort_bucket="L",
        blast_radius={
            "dependent_files": ["pkg/user.py"],
            "dependent_count": 1,
            "import_rewrites": 1,
        },
        confidence="high",
        source_biomarker="",
    )


def _write_source(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Source gathering
# ---------------------------------------------------------------------------


def test_gather_spans_reads_target_span(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/example.py", "\n".join(f"line {i}" for i in range(1, 31)))
    spans = _gather_spans(_extract_class_suggestion(), tmp_path)
    assert len(spans) == 1
    assert spans[0].file == "pkg/example.py"
    assert spans[0].start_line == 1 and spans[0].end_line == 20
    assert "line 1" in spans[0].source and "line 20" in spans[0].source


def test_gather_spans_extract_helper_reads_every_occurrence(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/a.py", "\n".join(f"a{i}" for i in range(1, 41)))
    _write_source(tmp_path, "pkg/b.py", "\n".join(f"b{i}" for i in range(1, 41)))
    sug = RefactoringSuggestion(
        refactoring_type="extract_helper",
        file_path="pkg/a.py",
        target_symbol="dup",
        line_start=5,
        line_end=15,
        plan={
            "occurrences": [
                {"file": "pkg/a.py", "line_start": 5, "line_end": 15},
                {"file": "pkg/b.py", "line_start": 10, "line_end": 20},
            ],
            "suggested_site": {"module": "pkg", "directory": "pkg"},
            "duplicated_lines": 10,
        },
        evidence={"occurrence_count": 2, "duplicated_lines": 10, "co_change_count": 4},
        impact_delta=0.6,
        effort_bucket="M",
        blast_radius={"files": ["pkg/a.py", "pkg/b.py"]},
        confidence="high",
        source_biomarker="dry_violation",
    )
    spans = _gather_spans(sug, tmp_path)
    files = {s.file for s in spans}
    assert files == {"pkg/a.py", "pkg/b.py"}


def test_gather_spans_split_file_reads_whole_module_and_dependent_heads(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/big.py", "\n".join(f"line{i} = {i}" for i in range(1, 401)))
    _write_source(tmp_path, "pkg/user.py", "\n".join(f"u{i}" for i in range(1, 100)))
    spans = _gather_spans(_split_file_suggestion(), tmp_path)
    by_file = {s.file: s for s in spans}
    assert set(by_file) == {"pkg/big.py", "pkg/user.py"}
    # The whole module is read — well past the 240-line default span cap.
    assert by_file["pkg/big.py"].end_line == 400
    # The dependent contributes only its import header.
    assert by_file["pkg/user.py"].end_line <= _MAX_IMPORT_HEAD_LINES


def test_user_prompt_split_file_carries_instruction_and_groups(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/big.py", "def parse_a():\n    return 1\n")
    spans = _gather_spans(_split_file_suggestion(), tmp_path)
    prompt = _build_user_prompt(_split_file_suggestion(), spans)
    assert "SPLIT FILE" in prompt
    assert "parsing" in prompt and "pkg/parsing.py" in prompt


def test_gather_spans_skips_path_escape(tmp_path: Path) -> None:
    sug = _extract_class_suggestion()
    sug.file_path = "../outside.py"
    assert _gather_spans(sug, tmp_path) == []


def test_user_prompt_carries_plan_and_source(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/example.py", "class GodClass:\n    pass\n")
    sug = _extract_class_suggestion()
    sug.line_end = 2
    spans = _gather_spans(sug, tmp_path)
    prompt = _build_user_prompt(sug, spans)
    assert "EXTRACT CLASS" in prompt
    assert "GodClass" in prompt
    assert "Structured plan" in prompt
    assert "class GodClass" in prompt


# ---------------------------------------------------------------------------
# Diff extraction
# ---------------------------------------------------------------------------


def test_extract_diff_pulls_fenced_block() -> None:
    content = "Summary\n\n```diff\n--- a/x.py\n+++ b/x.py\n@@\n-old\n+new\n```\n"
    diff = _extract_diff(content)
    assert diff.startswith("--- a/x.py")
    assert "+new" in diff


def test_extract_diff_absent_returns_empty() -> None:
    assert _extract_diff("no diff here") == ""


# ---------------------------------------------------------------------------
# End-to-end enrichment + caching
# ---------------------------------------------------------------------------


async def test_enrich_returns_result_and_caches(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/example.py", "class GodClass:\n    pass\n" + "x = 1\n" * 18)
    provider = MockProvider(responses=[GeneratedResponse("done\n```diff\n+a\n```", 10, 5)])
    sug = _extract_class_suggestion()

    result = await enrich_suggestion(sug, provider=provider, repo_path=tmp_path)
    assert isinstance(result, EnrichmentResult)
    assert result.refactoring_type == "extract_class"
    assert result.provider == "mock"
    assert result.diff == "+a"
    assert result.cached is False
    assert provider.call_count == 1
    assert result.spans and result.spans[0]["file"] == "pkg/example.py"

    # Second call with the same plan + source + model is served from cache.
    cached = await enrich_suggestion(sug, provider=provider, repo_path=tmp_path)
    assert cached.cached is True
    assert provider.call_count == 1  # no second generate()


async def test_enrich_no_cache_regenerates(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/example.py", "class GodClass:\n    pass\n")
    provider = MockProvider(responses=[GeneratedResponse("a", 1, 1), GeneratedResponse("b", 1, 1)])
    sug = _extract_class_suggestion()
    await enrich_suggestion(sug, provider=provider, repo_path=tmp_path, use_cache=False)
    await enrich_suggestion(sug, provider=provider, repo_path=tmp_path, use_cache=False)
    assert provider.call_count == 2


# ---------------------------------------------------------------------------
# LCOM4 self-check (Extract Class)
# ---------------------------------------------------------------------------


_TWO_COHESIVE_CLASSES = """\
Here is the split.

```python
class Getter:
    def __init__(self):
        self.x = 0

    def get(self):
        return self.x


class Putter:
    def __init__(self):
        self.y = 0

    def put(self, v):
        self.y = v
```
"""


async def test_extract_class_self_check_reports_improvement(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/example.py", "class GodClass:\n    pass\n")
    provider = MockProvider(responses=[GeneratedResponse(_TWO_COHESIVE_CLASSES, 10, 50)])
    result = await enrich_suggestion(
        _extract_class_suggestion(), provider=provider, repo_path=tmp_path
    )
    v = result.validation
    assert v["status"] == "checked"
    assert v["before_lcom4"] == 2
    assert v["class_count"] == 2
    assert v["after_max_lcom4"] == 1
    assert v["improved"] is True


async def test_self_check_skipped_when_no_code_blocks(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/example.py", "class GodClass:\n    pass\n")
    provider = MockProvider(responses=[GeneratedResponse("prose only, no code", 5, 5)])
    result = await enrich_suggestion(
        _extract_class_suggestion(), provider=provider, repo_path=tmp_path
    )
    assert result.validation["status"] == "skipped"


# ---------------------------------------------------------------------------
# Size + partition self-check (Split File)
# ---------------------------------------------------------------------------


_TWO_PARTITIONED_FILES = """\
Split done.

```python
def parse_a():
    return 1


def parse_b():
    return 2
```

```python
def render_a():
    return 3


def render_b():
    return 4
```
"""


_DUPLICATED_FILES = """\
```python
def parse_a():
    return 1
```

```python
def parse_a():
    return 1


def render_a():
    return 3
```
"""


async def test_split_file_self_check_reports_clean_partition(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/big.py", "def parse_a():\n    return 1\n" + "x = 1\n" * 400)
    provider = MockProvider(responses=[GeneratedResponse(_TWO_PARTITIONED_FILES, 10, 50)])
    result = await enrich_suggestion(
        _split_file_suggestion(), provider=provider, repo_path=tmp_path
    )
    v = result.validation
    assert v["status"] == "checked"
    assert v["file_count"] == 2
    assert v["partitioned"] is True
    assert v["duplicated_symbols"] == []
    assert v["all_below_floor"] is True
    assert v["improved"] is True


async def test_split_file_self_check_flags_duplicated_symbol(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/big.py", "def parse_a():\n    return 1\n")
    provider = MockProvider(responses=[GeneratedResponse(_DUPLICATED_FILES, 10, 50)])
    result = await enrich_suggestion(
        _split_file_suggestion(), provider=provider, repo_path=tmp_path
    )
    v = result.validation
    assert v["status"] == "checked"
    assert "parse_a" in v["duplicated_symbols"]
    assert v["partitioned"] is False
    assert v["improved"] is False


async def test_split_file_self_check_skipped_for_single_block(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/big.py", "def parse_a():\n    return 1\n")
    provider = MockProvider(
        responses=[GeneratedResponse("```python\ndef parse_a():\n    return 1\n```", 5, 5)]
    )
    result = await enrich_suggestion(
        _split_file_suggestion(), provider=provider, repo_path=tmp_path
    )
    assert result.validation["status"] == "skipped"


async def test_self_check_skipped_for_non_extract_class(tmp_path: Path) -> None:
    _write_source(tmp_path, "pkg/a.py", "x = 1\n")
    sug = RefactoringSuggestion(
        refactoring_type="break_cycle",
        file_path="pkg/a.py",
        target_symbol="cycle",
        line_start=None,
        line_end=None,
        plan={
            "cycle": ["pkg/a.py", "pkg/b.py"],
            "cut_edges": [{"from": "pkg/a.py", "to": "pkg/b.py"}],
        },
        evidence={"cycle_size": 2},
        impact_delta=0.0,
        effort_bucket="M",
        blast_radius={"files": ["pkg/a.py", "pkg/b.py"]},
        confidence="medium",
    )
    provider = MockProvider(responses=[GeneratedResponse("fix", 1, 1)])
    result = await enrich_suggestion(sug, provider=provider, repo_path=tmp_path)
    assert result.validation == {}


# ---------------------------------------------------------------------------
# Config gate
# ---------------------------------------------------------------------------


def test_llm_enrichment_enabled_gate() -> None:
    assert llm_enrichment_enabled({"refactoring": {"llm": {"enabled": True}}}) is True
    # Only an explicit false disables it; an unset key defaults on (the local
    # serve experience works without a config trip).
    assert llm_enrichment_enabled({"refactoring": {"llm": {"enabled": False}}}) is False
    assert llm_enrichment_enabled({"refactoring": {}}) is True
    assert llm_enrichment_enabled({}) is True


def test_validate_extract_method_detects_ccn_drop():
    # The original function (high CCN) is the span fed to the model; the
    # generated response splits it into a low-CCN residual plus a helper.
    original = (
        "def process(items, t):\n"
        "    total = 0\n"
        "    for v in items:\n"
        "        if v > t:\n"
        "            total += v\n"
        "        elif v < 0:\n"
        "            total -= v\n"
        "    return total\n"
    )
    spans = [SourceSpan(file="m.py", start_line=1, end_line=8, source=original)]
    content = (
        "Summary.\n\n"
        "```python\n"
        "def _accumulate(items, t):\n"
        "    total = 0\n"
        "    for v in items:\n"
        "        if v > t:\n"
        "            total += v\n"
        "        elif v < 0:\n"
        "            total -= v\n"
        "    return total\n\n"
        "def process(items, t):\n"
        "    return _accumulate(items, t)\n"
        "```\n"
    )
    result = _validate_extract_method(content, "m.py", spans, "process")
    if result.get("status") == "skipped":
        import pytest

        pytest.skip("tree-sitter python pack missing")
    assert result["function_count"] >= 2
    assert result["residual_ccn"] < result["original_ccn"]
    assert result["improved"] is True
