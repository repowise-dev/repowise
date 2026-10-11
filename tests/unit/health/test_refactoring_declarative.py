"""A flat record builder gets no Extract Method plan: its CCN comes from one
independent guard per field, and no helper makes either half simpler."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file
from repowise.core.analysis.health.refactoring import extract_method
from repowise.core.analysis.health.refactoring.declarative import declarative_flat
from repowise.core.analysis.health.refactoring.extract_method import ExtractMethodDetector
from repowise.core.analysis.health.refactoring.models import RefactoringContext


@dataclass
class _Finding:
    biomarker_type: str
    function_name: str
    line_start: int
    health_impact: float


def _functions(language: str, ext: str, src: str):
    from repowise.core.ingestion.parser import _get_language

    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    res = analyze_file(f"m.{ext}", language, textwrap.dedent(src).encode(), flagged_only=False)
    return res.functions


def _flat(src: str, language: str = "python", ext: str = "py") -> bool:
    (fn,) = [f for f in _functions(language, ext, src) if f.name == "build"]
    return declarative_flat(fn.fn_node, get_language_map(language))


# The configuration-summary shape: rows added behind one guard each.
_SUMMARY = """
def build(result, patterns, allow_fast, generate_docs=True):
    \"\"\"The summary rows.\"\"\"
    summary = []

    def add(label, value):
        summary.append((label, value))

    # Indexing
    add("Generate docs", "yes" if generate_docs else "no")
    add("Skip tests", "yes" if result["skip_tests"] else "no")
    if result["include_submodules"]:
        add("Include submodules", "yes")
    if allow_fast:
        add("Run mode", result["run_mode"])
    if patterns:
        if len(patterns) <= 5:
            add("Exclude", ", ".join(patterns))
        else:
            add("Exclude", "\\n".join(patterns))
    cap = result.get("max_file_pages")
    if cap:
        add("File pages", f"top {cap:,}")
    elif cap == 0:
        add("File pages", "all")
    if generate_docs:
        add("Concurrency", str(result["concurrency"]))
        if result.get("reasoning"):
            add("Reasoning", result["reasoning"])
        add("Embedder", result["embedder"])
    return summary
"""

# Query parameters set when given, then one request.
_QUERY_TS = """
export async function build(opts: Opts): Promise<Result> {
  const q = new URLSearchParams();
  if (opts.format) {
    q.set("format", opts.format);
  }
  if (typeof opts.limit === "number") {
    q.set("limit", String(opts.limit));
  }
  if (opts.selector?.trim()) {
    q.set("selector", opts.selector.trim());
  }
  if (opts.labels === true) q.set("labels", "1");
  return await fetchJson(`/snapshot?${q.toString()}`);
}
"""

# Fields copied when present, a guard clause first, one per-row loop.
_RECORD_TS = """
function build(raw: unknown, rows: Row[]): Settings | null {
  if (!raw) {
    return null;
  }
  const s: Settings = structuredClone(DEFAULTS);
  if (typeof raw.ttl === "string") {
    try {
      s.ttlMs = parseDuration(raw.ttl);
    } catch {
      // keep the default
    }
  }
  if (raw.tools) {
    s.tools = raw.tools;
  }
  if (raw.claims === null) {
    delete s.claims;
  }
  for (const row of rows) {
    s.items.push({ id: row.id, title: row.title ?? "untitled" });
  }
  return s;
}
"""


@pytest.mark.parametrize(
    ("src", "language", "ext"),
    [(_SUMMARY, "python", "py"), (_QUERY_TS, "typescript", "ts"), (_RECORD_TS, "typescript", "ts")],
)
def test_a_flat_record_builder_is_declarative(src, language, ext):
    assert _flat(src, language, ext)


@pytest.mark.parametrize(
    "change",
    [
        # A second name written is two jobs, not one record.
        ('    add("Skip tests"', '    other.append(1)\n    add("Skip tests"'),
        # A local the guards update is state, not a field.
        ('    cap = result.get("max_file_pages")\n', '    cap = 1\n    cap = result.get("x")\n'),
        # A loop inside a loop walks a structure.
        (
            "    if allow_fast:\n",
            "    for p in patterns:\n        for q in p:\n            add(p, q)\n    if allow_fast:\n",
        ),
        # A call made for what it does, to anything else.
        ("    if allow_fast:\n", "    notify(result)\n    if allow_fast:\n"),
    ],
)
def test_logic_beside_the_writes_is_not_declarative(change):
    old, new = change
    assert old in _SUMMARY
    assert not _flat(_SUMMARY.replace(old, new, 1))


def test_the_detector_refuses_a_flat_builder_and_keeps_a_real_split(monkeypatch):
    src = _SUMMARY + textwrap.dedent(
        """

        def process(records, threshold):
            results = []
            errors = 0
            for r in records:
                if r is None:
                    errors += 1
                    continue
                results.append(r)
            total = 0
            count = 0
            for v in results:
                if v > threshold:
                    total += v
                    count += 1
                else:
                    total -= v
            average = total / count if count else 0
            return average, errors
        """
    )
    fns = _functions("python", "py", src)
    ctx = RefactoringContext(
        file_path="m.py",
        language="python",
        nloc=100,
        findings=[_Finding("complex_method", f.name, f.start_line, 1.5) for f in fns],
        function_analyses=fns,
    )
    assert [s.target_symbol for s in ExtractMethodDetector().detect(ctx)] == ["process"]
    # The builder has a span the slicer would offer: the refusal is the shape.
    monkeypatch.setattr(extract_method, "declarative_flat", lambda *_: False)
    found = sorted(s.target_symbol for s in ExtractMethodDetector().detect(ctx))
    assert found == ["build", "process"]
