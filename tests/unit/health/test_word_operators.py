"""Word operators that are not logic add no decision point.

Scala ``Boolean`` has no ``and`` / ``or`` methods, so an infix ``and`` is a
DSL's (ScalaTest matchers, Spark ``Column``). Pascal ``and`` / ``or`` on
integers masks bits.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_EXT = {"scala": "scala", "pascal": "pas"}


def _fns(language: str, source: str) -> dict:
    fcx = walk_file(f"/tmp/sample.{_EXT[language]}", language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}


_SCALA = """
object Spec {
  def matchers(result: Int) = {
    result should (be > 1 and be < 5)
    result should (equal(2) or equal(3))
  }

  def columns(df: DataFrame) =
    df.filter(col("a") > 1 and col("b") < 2 or col("c").isNull)

  def logic(a: Boolean, b: Boolean, c: Boolean) =
    if (a && b || c) 1 else 2
}
"""


def test_scala_infix_and_or_is_a_dsl_not_a_decision() -> None:
    fns = _fns("scala", _SCALA)
    assert fns["matchers"].ccn == 1
    assert fns["columns"].ccn == 1
    assert not fns["columns"].complex_conditions


def test_scala_symbolic_operators_still_count() -> None:
    fn = _fns("scala", _SCALA)["logic"]
    assert fn.ccn == 4


_PASCAL = """
unit U;

implementation

procedure Masks;
begin
  if (Flags and $FF) <> 0 then X := 1;
  Y := (Z shr 4) and 15;
  if (Flags and Mask) = 0 then X := 2;
  Y := Mask or $10;
end;

procedure Logic;
begin
  if A and B then X := 1;
  if (A > 1) or not B then X := 2;
  Ok := HasKey and (Count > 0);
end;

end.
"""


def test_pascal_bitwise_and_or_is_not_a_decision() -> None:
    fn = _fns("pascal", _PASCAL)["Masks"]
    assert fn.ccn == 3


def test_pascal_boolean_and_or_still_counts() -> None:
    fn = _fns("pascal", _PASCAL)["Logic"]
    assert fn.ccn == 6
