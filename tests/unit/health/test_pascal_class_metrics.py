"""Object Pascal class metrics: a class is declared in the interface section and
its methods are implemented outside it as ``procedure TFoo.Bar``. The two are
paired by the qualified name, case-insensitively."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

SOURCE = """unit Sample;

interface

type
  TLater = class;

  TSplit = class
  private
    FA1: Integer;
    FA2: Integer;
    FB1: Integer;
    FB2: Integer;
  public
    procedure SetA(V: Integer);
    function GetA: Integer;
    procedure SetB(V: Integer);
    function GetB: Integer;
  end;

  TCohesive = class
  private
    FX: Integer;
    FY: Integer;
  public
    procedure Inc1;
    function Total: Integer;
    function Sum2: Integer;
  end;

  TLater = class
  private
    FQ: Integer;
    FR: Integer;
  public
    procedure Touch;
  end;

implementation

procedure TSplit.SetA(V: Integer);
begin
  FA1 := V;
  FA2 := V;
end;

function TSplit.GetA: Integer;
begin
  Result := FA1 + FA2;
end;

procedure TSplit.SetB(V: Integer);
begin
  Self.FB1 := V;
  Self.FB2 := V;
end;

function TSplit.GetB: Integer;
begin
  Result := FB1 + FB2;
end;

procedure TCohesive.Inc1;
begin
  Inc(FX);
  FY := FX;
end;

function TCohesive.Total: Integer;
begin
  Result := FX + FY;
end;

function tcohesive.Sum2: Integer;
begin
  Result := Total * 2;
end;

procedure TLater.Touch;
begin
  FQ := FR;
end;

procedure Free1;
begin
end;

end.
"""


def _classes(source: str = SOURCE) -> dict:
    fcx = walk_file("Sample.pas", "pascal", source.encode())
    if not fcx.classes:
        pytest.skip("pascal class metrics unavailable")
    return {c.name: c for c in fcx.classes}


def test_two_unrelated_field_groups_split_the_class():
    split = _classes()["TSplit"]
    assert split.method_count == 4
    assert split.field_count == 4
    assert split.lcom4 == 2


def test_methods_sharing_fields_and_siblings_are_one_component():
    cohesive = _classes()["TCohesive"]
    assert cohesive.method_count == 3
    assert cohesive.lcom4 == 1


def test_out_of_line_method_matches_the_class_case_insensitively():
    # ``tcohesive.Sum2`` reaches the class only by its name, in another case.
    assert "Sum2" in {m for g in _classes()["TCohesive"].components for m in g.methods}


def test_forward_declaration_does_not_take_the_methods():
    classes = _classes()
    assert classes["TLater"].method_count == 1
    assert len([c for c in walk_file("Sample.pas", "pascal", SOURCE.encode()).classes if c.name == "TLater"]) == 1


def test_free_routine_is_not_a_class_method():
    fcx = walk_file("Sample.pas", "pascal", SOURCE.encode())
    assert all("Free1" not in {m for g in c.components for m in g.methods} for c in fcx.classes)


def _walk(source: str) -> dict:
    return _classes(source)


CASE = """unit Sample;
interface
type
  TCase = class
  private
    FA1: Integer;
    FA2: Integer;
    FB1: Integer;
    FB2: Integer;
  public
    procedure SetA(V: Integer);
    function GetA: Integer;
    procedure SetB(V: Integer);
    function GetB: Integer;
    function Shadow(fa1: Integer): Integer;
  end;
implementation
procedure TCase.SetA(V: Integer);
begin
  fa1 := V;
  Fa2 := V;
end;
function TCase.GetA: Integer;
begin
  Result := FA1 + fA2;
end;
procedure TCase.SetB(V: Integer);
begin
  fb1 := V;
  FB2 := V;
end;
function TCase.GetB: Integer;
begin
  Result := Fb1 + fb2;
end;
function TCase.Shadow(fa1: Integer): Integer;
begin
  Result := FA1;
end;
end.
"""


def test_field_references_in_another_case_still_link_methods():
    case = _walk(CASE)["TCase"]
    assert case.field_count == 4
    assert case.lcom4 == 2
    assert [sorted(g.fields) for g in case.components] == [["FA1", "FA2"], ["FB1", "FB2"]]


def test_parameter_in_another_case_still_shadows_the_field():
    case = _walk(CASE)["TCase"]
    assert all("Shadow" not in g.methods for g in case.components)


PROPS = """unit Sample;
interface
type
  TProps = class
  private
    FA1: Integer;
    FA2: Integer;
    FB1: Integer;
    FB2: Integer;
    FV1: Integer;
    FV2: Integer;
    procedure SetVal(N: Integer);
  public
    procedure Both;
    procedure Touch;
    procedure UseVal;
    procedure Other;
    property PA1: Integer read FA1;
    property PA2: Integer read FA2 write FA2;
    property Val: Integer read FV1 write SetVal;
  end;
implementation
procedure TProps.SetVal(N: Integer);
begin
  FV1 := N;
  FV2 := N;
end;
procedure TProps.Both;
begin
  PA1 := PA2;
end;
procedure TProps.Touch;
begin
  FA1 := FA2;
end;
procedure TProps.UseVal;
begin
  Val := 3;
end;
procedure TProps.Other;
begin
  FB1 := FB2;
end;
end.
"""


def test_property_reference_reaches_the_field_it_reads_and_writes():
    props = _walk(PROPS)["TProps"]
    groups = {tuple(g.methods): g for g in props.components}
    both_group = next(g for g in props.components if "Both" in g.methods)
    assert set(both_group.methods) == {"Both", "Touch"}
    assert both_group.fields == ["FA1", "FA2"]
    # The property name is an alias, never a field of its own.
    assert all(not {"PA1", "PA2", "Val"} & set(g.fields) for g in groups.values())


def test_property_writing_through_a_setter_joins_the_setter_component():
    props = _walk(PROPS)["TProps"]
    val_group = next(g for g in props.components if "UseVal" in g.methods)
    assert "SetVal" in val_group.methods
    assert props.lcom4 == 3


CONTRACTS = """unit Sample;
interface
type
  TBase = class
    procedure Run; virtual;
  end;
  TDerived = class(TBase)
  private
    FA: Integer;
    FB: Integer;
    FC: Integer;
    FD: Integer;
    FE: Integer;
    FF: Integer;
  public
    constructor Create;
    destructor Destroy; override;
    procedure Run; override;
    procedure M1;
    procedure M2;
  end;
implementation
constructor TDerived.Create;
begin
  FA := 1;
  FB := 2;
end;
destructor TDerived.Destroy;
begin
  FA := 0;
  FB := 0;
end;
procedure TDerived.Run;
begin
  FE := FF;
end;
procedure TDerived.M1;
begin
  FC := FD;
end;
procedure TDerived.M2;
begin
  FD := FC;
end;
end.
"""


def test_constructors_destructors_and_overrides_are_not_a_responsibility_of_their_own():
    derived = _walk(CONTRACTS)["TDerived"]
    assert derived.lcom4 == 1
    assert [set(g.methods) for g in derived.components] == [{"M1", "M2"}]
