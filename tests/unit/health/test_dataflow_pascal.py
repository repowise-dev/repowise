"""Pascal def/use, CFG shapes and Extract Method."""

from __future__ import annotations

import textwrap

import pytest
import tree_sitter

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions
from repowise.core.analysis.health.dataflow.dialects import pascal as dialect
from repowise.core.analysis.health.dataflow.slice_writes import _writes_var
from repowise.core.ingestion.parser import _get_language

pytest.importorskip("tree_sitter_pascal", reason="run `uv sync --all-packages`")


def _first(src: str):
    res = analyze_file("m.pas", "pascal", textwrap.dedent(src).encode(), flagged_only=False)
    assert res.functions, "no routine analysed"
    return res.functions[0]


def _defs(fn) -> set[tuple[str, int]]:
    return {(d.var, d.line) for d in fn.def_use.definitions}


def _uses(fn) -> set[tuple[str, int]]:
    return {(u.name, u.line) for b in fn.def_use.blocks.values() for u in b.uses}


_SHAPES = """\
unit U;
interface
implementation
function TFoo.Bar(const A: Integer): Integer;
var
  I, J: Integer;
  S: string;
begin
  var K := 5;
  for J := 0 to A do
    S := S + IntToStr(J);
  for var X in Items do
    Use(X);
  Inc(I);
  with Obj do
    Count := I;
  if A > 0 then I := 1 else I := 2;
  try
    Obj.Field := i;
  except
    on E: Exception do Log(E.Message);
  end;
  Result := I + K;
end;
end.
"""


def test_write_sites_are_defs() -> None:
    defs = _defs(_first(_SHAPES))
    assert {
        ("A", 4),  # parameter
        ("K", 9),  # inline var
        ("J", 10),  # for counter
        ("S", 11),  # single-statement for body
        ("X", 12),  # for-in iterator
        ("I", 17),  # if then/else arms
        ("E", 21),  # exception handler binder
        ("Result", 23),
    } <= defs


def test_a_field_target_reads_its_receiver_and_binds_no_local() -> None:
    fn = _first(_SHAPES)
    assert ("Obj", 19) in _uses(fn)
    assert not any(var in ("Field", "Obj") for var, _ in _defs(fn))


def test_a_call_argument_and_a_write_inside_with_are_may_defs() -> None:
    fn = _first(_SHAPES)
    defs, uses = _defs(fn), _uses(fn)
    assert ("I", 14) in defs and ("I", 14) in uses  # Inc(I): a var parameter
    assert ("Count", 16) in defs and ("Count", 16) in uses  # with Obj do Count := I


def test_names_take_their_declared_spelling() -> None:
    assert ("I", 19) in _uses(_first(_SHAPES))  # written ``i``


_PROCESS = """\
unit Demo;
interface
implementation
function Process(const Records: TArray<Integer>; Threshold: Integer): Integer;
var
  Errors, R, V, Total, Count, Average: Integer;
begin
  Errors := 0;
  for R in Records do
  begin
    if R < 0 then
      Errors := Errors + 1;
  end;
  Total := 0;
  Count := 0;
  for V in Records do
  begin
    if V > Threshold then
    begin
      Total := Total + V;
      Count := Count + 1;
    end
    else
      Total := Total - V;
  end;
  Average := 0;
  if Count > 0 then
    Average := Total div Count;
  Result := Average + Errors;
end;
end.
"""


def test_extract_method_fires() -> None:
    extractions = find_extractions(_first(_PROCESS), get_language_map("pascal"))
    assert extractions, "expected at least one Pascal extraction"
    best = extractions[0]
    assert best.returns == ("Average",)
    assert {"Records", "Threshold"} <= set(best.params)
    assert best.ccn_removed >= 1


def test_extractions_are_deterministic() -> None:
    lmap = get_language_map("pascal")
    first = find_extractions(_first(_PROCESS), lmap)
    second = find_extractions(_first(_PROCESS), lmap)
    assert [(e.start_line, e.end_line, e.params, e.returns) for e in first] == [
        (e.start_line, e.end_line, e.params, e.returns) for e in second
    ]


def _variant(old: str, new: str) -> str:
    assert old in _PROCESS
    return _PROCESS.replace(old, new)


# The tail computes the function's value straight into ``Result``.
_RESULT_TAIL = _variant(
    "  Average := 0;\n  if Count > 0 then\n    Average := Total div Count;\n"
    "  Result := Average + Errors;\n",
    "  Result := Errors;\n  if Count > 0 then\n    Result := Result + Total div Count;\n",
)


def test_result_is_read_at_exit_so_a_tail_writing_it_returns_it() -> None:
    fn = _first(_RESULT_TAIL)
    assert "Result" in {u.name for u in fn.def_use.blocks[fn.cfg.exit_id].uses}
    tail = _RESULT_TAIL.splitlines().index("  Result := Errors;") + 1
    plans = [
        p for p in find_extractions(fn, get_language_map("pascal")) if p.start_line <= tail <= p.end_line
    ]
    assert plans, "expected a plan over the tail"
    assert all("Result" in p.returns for p in plans)


def test_var_and_out_parameters_are_read_at_exit() -> None:
    fn = _first(
        "unit U;\ninterface\nimplementation\n"
        "procedure P(var A: Integer; out B: string; C: Integer);\n"
        "begin\n  A := C;\n  B := '';\nend;\nend.\n"
    )
    assert {u.name for u in fn.def_use.blocks[fn.cfg.exit_id].uses} == {"A", "B"}


# ``Exit`` inside the tail the clean extraction would lift.
_EARLY_EXIT = _variant(
    "  Average := 0;\n",
    "  Average := 0;\n  if Total < 0 then\n    Exit;\n",
)


def test_a_span_holding_exit_is_never_extracted() -> None:
    exit_line = _EARLY_EXIT.splitlines().index("    Exit;") + 1
    plans = find_extractions(_first(_EARLY_EXIT), get_language_map("pascal"))
    assert plans, "the loops before the exit still extract"
    assert not any(p.start_line <= exit_line <= p.end_line for p in plans)



# A span storing into fields of a parameter, never into the parameter.
_FIELD_STORE = """unit Demo;
interface
implementation
procedure Fill(Stats: TStats; const Records: TArray<Integer>; Threshold: Integer);
var
  V, Total: Integer;
begin
  Stats.Count := 0;
  Total := 0;
  for V in Records do
  begin
    if V > Threshold then
    begin
      Total := Total + V;
      Stats.Count := Stats.Count + 1;
    end
    else
      Total := Total - V;
  end;
  if Total > Threshold then
    Stats.Flag := True
  else
    Stats.Flag := False;
  Log(Total);
end;
end.
"""


def test_a_field_target_writes_no_local() -> None:
    tree = tree_sitter.Parser(_get_language("pascal")).parse(_FIELD_STORE.encode())
    stores = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "assignment" and node.text.startswith(b"Stats.Flag"):
            stores.append(node)
        stack.extend(node.children)
    assert stores
    assert not any(_writes_var(store, "Stats") for store in stores)


def test_a_span_storing_into_fields_takes_their_base_as_a_parameter() -> None:
    flag = _FIELD_STORE.splitlines().index("    Stats.Flag := True") + 1
    plans = [
        p
        for p in find_extractions(_first(_FIELD_STORE), get_language_map("pascal"))
        if p.start_line <= flag <= p.end_line
    ]
    assert plans, "expected a plan over the field stores"
    assert all("Stats" in p.params and "Stats" not in p.returns for p in plans)


def test_declared_spellings_are_read_once_per_routine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = dialect._declared_spellings

    def counting(routine):
        calls.append(routine.start_byte)
        return real(routine)

    monkeypatch.setattr(dialect, "_declared_spellings", counting)
    monkeypatch.setattr(dialect.DIALECT, "_last_spellings", None)
    _first(_PROCESS)
    assert len(calls) == 1
