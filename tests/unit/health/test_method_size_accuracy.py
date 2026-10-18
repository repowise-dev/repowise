"""large_method and complex_method read the CCN that drives severity and count code, not markup."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.complex_method import ComplexMethodDetector
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file


def _ctx(*functions: FunctionComplexity, path: str = "src/mod.py") -> FileContext:
    return FileContext(
        file_path=path, language="python", nloc=200, has_test_file=False, module=None,
        all_functions=tuple(functions),
    )


def _fn(**kw) -> FunctionComplexity:
    base = {"name": "tokenize", "start_line": 10, "end_line": 109, "ccn": 4,
            "max_nesting": 1, "cognitive": 3, "nloc": 80}
    return FunctionComplexity(**{**base, **kw})


def test_large_method_skips_a_long_dispatch_whose_judged_ccn_is_flat() -> None:
    # Raw CCN 10, all of it one dispatch of one-line arms: judged CCN 1.
    dispatch = _fn(ccn=10, dispatch_share=1.0, dispatch_arm=0)
    assert LargeMethodDetector().detect(_ctx(dispatch)) == []


def test_large_method_reason_separates_code_lines_from_the_span() -> None:
    [finding] = LargeMethodDetector().detect(_ctx(_fn()))
    assert finding.reason == "tokenize is 80 lines of code (100 with blanks and comments)"
    assert finding.details["span"] == 100


def test_complex_method_prints_the_judged_ccn_beside_the_raw_one() -> None:
    # 30 - round(0.6 * 29) + 2 = 15: severity HIGH comes from the judged value.
    fn = _fn(ccn=30, dispatch_share=0.6, dispatch_arm=2)
    [finding] = ComplexMethodDetector().detect(_ctx(fn))
    assert finding.severity == "high"
    assert finding.reason == (
        "tokenize has cyclomatic complexity 15 (30 counting every arm of its dispatch)"
    )
    assert finding.details["ccn"] == 30
    assert finding.details["judged_ccn"] == 15


def test_complex_method_reason_is_unchanged_without_a_dominant_dispatch() -> None:
    [finding] = ComplexMethodDetector().detect(_ctx(_fn(ccn=12)))
    assert finding.reason == "tokenize has cyclomatic complexity 12"
    assert "judged_ccn" not in finding.details


_ROWS = "\n".join(
    f'        <li className="row-{i}">\n          <span>Row {i}</span>\n        </li>'
    for i in range(25)
)
_COMPONENT = f"""export function Panel({{ items, open }}: Props) {{
  if (!open) {{
    return null;
  }}
  const label = items.length > 0 ? "Items" : "Empty";
  return (
    <section className="panel">
      <h2>{{label}}</h2>
      <ul>
{_ROWS}
      </ul>
    </section>
  );
}}
"""


def test_large_method_does_not_count_jsx_markup_lines() -> None:
    fcx = walk_file("src/Panel.tsx", "typescript", _COMPONENT.encode())
    [panel] = [fn for fn in fcx.functions if fn.name == "Panel"]
    assert panel.nloc >= 80
    assert panel.markup_nloc >= 75
    assert LargeMethodDetector().detect(_ctx(panel, path="src/Panel.tsx")) == []


def test_logic_lines_inside_jsx_still_count_as_code() -> None:
    fcx = walk_file("src/Panel.tsx", "typescript", _COMPONENT.encode())
    [panel] = [fn for fn in fcx.functions if fn.name == "Panel"]
    # ``<h2>{label}</h2>`` holds an expression, so it is code.
    assert panel.nloc - panel.markup_nloc >= 6
