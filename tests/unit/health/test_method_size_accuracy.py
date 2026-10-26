"""large_method and complex_method read the CCN that drives severity and count code, not markup."""

from __future__ import annotations

from dataclasses import replace

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.brain_method import BrainMethodDetector
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


def _central(*functions: FunctionComplexity) -> FileContext:
    """A file that clears brain_method's centrality gate (floor defaults to 8)."""
    return replace(_ctx(*functions), dependents_count=10)


def test_brain_method_prints_the_judged_ccn_beside_the_raw_one() -> None:
    # The severity is graded on judged_ccn(fn), so the reason quotes it too.
    fn = _fn(ccn=30, dispatch_share=0.6, dispatch_arm=2)
    [finding] = BrainMethodDetector().detect(_central(fn))
    assert finding.severity == "high"
    assert finding.reason == (
        "Brain Method: tokenize is 80 lines, CCN 15 "
        "(30 counting every arm of its dispatch), in a file imported by 10 others"
    )
    assert finding.details["ccn"] == 30
    assert finding.details["judged_ccn"] == 15


def test_brain_method_reason_is_unchanged_without_a_dominant_dispatch() -> None:
    [finding] = BrainMethodDetector().detect(_central(_fn(ccn=12)))
    assert finding.reason == (
        "Brain Method: tokenize is 80 lines, CCN 12, in a file imported by 10 others"
    )
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


def _code_rows(component: str, name: str = "Card") -> FunctionComplexity:
    fcx = walk_file("src/Card.tsx", "typescript", component.encode())
    [fn] = [fn for fn in fcx.functions if fn.name == name]
    return fn


_STYLE_PAIRS = "\n".join(f'        prop{i}: "value-{i}",' for i in range(70))
_SPANS = "\n".join(
    f'      <span\n        className="cell-{i}"\n        style={{{{ opacity: 0.5 }}}}\n'
    f'        title={{label}}\n      />'
    for i in range(20)
)
_STYLED = f"""export function Card({{ label, open }}: Props) {{
  if (!open) {{
    return null;
  }}
  const tone = label ? "on" : "off";
  if (tone === "on") {{
    console.log(tone);
  }}
  return (
    <div
      style={{{{
{_STYLE_PAIRS}
      }}}}
    >
{_SPANS}
    </div>
  );
}}
"""


def test_literal_style_objects_and_data_props_are_markup() -> None:
    card = _code_rows(_STYLED)
    # The function line, the guards, the const, the return and its parens.
    assert card.nloc - card.markup_nloc <= 12
    assert LargeMethodDetector().detect(_ctx(card, path="src/Card.tsx")) == []


def test_a_prop_with_a_call_or_a_condition_stays_code() -> None:
    card = _code_rows(
        """export function Card({ label, wide }: Props) {
  return (
    <div
      className="card"
      title={format(label)}
      style={{
        width: wide ? 320 : 260,
      }}
      onClick={() => {
        track(label);
      }}
    />
  );
}
"""
    )
    # Only ``<div``, ``className`` and ``/>`` are markup.
    assert card.markup_nloc == 3


def test_a_const_data_literal_used_only_as_a_prop_is_markup() -> None:
    card = _code_rows(
        """export function Card({ label }: Props) {
  const box = {
    padding: 4,
    color: "red",
  };
  return <div style={box}>{label}</div>;
}
"""
    )
    # The four lines of ``box`` are markup; the return line holds ``{label}``.
    assert card.markup_nloc == 4


@pytest.mark.parametrize("cast", ["as Palette", "satisfies Palette"])
def test_a_typed_const_data_literal_used_only_as_a_prop_is_markup(cast: str) -> None:
    card = _code_rows(
        f"""export function Card({{ label }}: Props) {{
  const box = {{
    padding: 4,
    color: "red",
  }} {cast};
  return <div style={{box}}>{{label}}</div>;
}}
"""
    )
    assert card.markup_nloc == 4


def test_a_const_data_literal_read_by_logic_stays_code() -> None:
    card = _code_rows(
        """export function Card({ kind }: Props) {
  const labels = {
    a: "Alpha",
    b: "Beta",
  };
  return <div title={labels.a}>{labels[kind]}</div>;
}
"""
    )
    assert card.markup_nloc == 0


def test_brain_method_does_not_count_jsx_markup_lines() -> None:
    # 100 lines, 40 of them markup: 60 lines of code is under brain_method's 70.
    assert BrainMethodDetector().detect(_central(_fn(nloc=100, ccn=12, markup_nloc=40))) == []
    [finding] = BrainMethodDetector().detect(_central(_fn(nloc=120, ccn=12, markup_nloc=40)))
    assert finding.reason == (
        "Brain Method: tokenize is 80 lines besides 40 of JSX markup, CCN 12, "
        "in a file imported by 10 others"
    )
    assert finding.details["markup_nloc"] == 40


def test_jsx_files_and_as_const_literals_read_the_same() -> None:
    component = """export function Card({ label }) {
  const box = {
    margin: -1,
  } as const;
  return <div style={box}>{label}</div>;
}
"""
    assert _code_rows(component).markup_nloc == 3
    plain = component.replace(" as const", "")
    fcx = walk_file("src/Card.jsx", "javascript", plain.encode())
    [card] = [fn for fn in fcx.functions if fn.name == "Card"]
    assert card.markup_nloc == 3
