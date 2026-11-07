"""Pascal calls resolve through field, parameter and local types, Self, inherited and properties."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

pytest.importorskip("tree_sitter_pascal", reason="run `uv sync --all-packages`")

TOAST = """\
unit uToast;
interface
type
  TToast = class
  public
    procedure HideTag(const ATag: string);
    procedure ShowRequest(const AText: string);
  end;
implementation
procedure TToast.HideTag(const ATag: string);
begin
end;
procedure TToast.ShowRequest(const AText: string);
begin
end;
end.
"""

MENUS = """\
unit uMenus;
interface
type
  TMenus = class
  private
    FText: string;
    function GetStubText: string;
    procedure SetStubText(const AValue: string);
  public
    property StubText: string read GetStubText write SetStubText;
  end;
implementation
function TMenus.GetStubText: string;
begin
  Result := FText;
end;
procedure TMenus.SetStubText(const AValue: string);
begin
  FText := AValue;
end;
end.
"""

BASE = """\
unit uBase;
interface
type
  TBase = class
  public
    procedure Paint; virtual;
    procedure Reset; virtual;
  end;
implementation
procedure TBase.Paint;
begin
end;
procedure TBase.Reset;
begin
end;
end.
"""

WINDOW = """\
unit uWindow;
interface
uses
  uBase, uToast, uMenus;
type
  TWindow = class(TBase)
  private
    FToast: TToast;
    FMenus: TMenus;
    procedure Flush;
  public
    procedure Paint; override;
    procedure Reset; override;
    procedure Notify(AOther: TToast);
  end;
implementation
procedure TWindow.Flush;
begin
end;
procedure TWindow.Paint;
begin
  inherited;
  FToast.HideTag('x');
  ftoast.showrequest('y');
  Self.Flush;
end;
procedure TWindow.Reset;
var
  LMenus, LOther: TMenus;
begin
  inherited Reset;
  Flush;
  FMenus.StubText := 'title';
  LOther.StubText := LMenus.StubText;
end;
procedure TWindow.Notify(AOther: TToast);
begin
  AOther.HideTag('z');
end;
end.
"""


@pytest.fixture(scope="module")
def graph(tmp_path_factory: pytest.TempPathFactory):
    repo = tmp_path_factory.mktemp("pascal_calls")
    for name, text in {
        "uToast.pas": TOAST,
        "uMenus.pas": MENUS,
        "uBase.pas": BASE,
        "uWindow.pas": WINDOW,
    }.items():
        (repo / name).write_text(text, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _callees(graph, caller: str) -> set[str]:
    return {
        callee
        for _, callee, data in graph.out_edges(caller, data=True)
        if data.get("edge_type") == "calls"
    }


def test_a_call_on_a_field_resolves_through_the_field_type(graph) -> None:
    assert "uToast.pas::TToast::HideTag" in _callees(graph, "uWindow.pas::TWindow::Paint")


def test_names_match_whatever_case_they_are_written_in(graph) -> None:
    assert "uToast.pas::TToast::ShowRequest" in _callees(graph, "uWindow.pas::TWindow::Paint")


def test_a_call_on_a_parameter_resolves_through_its_type(graph) -> None:
    assert "uToast.pas::TToast::HideTag" in _callees(graph, "uWindow.pas::TWindow::Notify")


def test_self_and_a_bare_call_reach_the_own_class_method(graph) -> None:
    assert "uWindow.pas::TWindow::Flush" in _callees(graph, "uWindow.pas::TWindow::Paint")
    assert "uWindow.pas::TWindow::Flush" in _callees(graph, "uWindow.pas::TWindow::Reset")


def test_inherited_reaches_the_base_method_not_the_override(graph) -> None:
    paint = _callees(graph, "uWindow.pas::TWindow::Paint")
    reset = _callees(graph, "uWindow.pas::TWindow::Reset")

    assert "uBase.pas::TBase::Paint" in paint
    assert "uBase.pas::TBase::Reset" in reset
    assert "uWindow.pas::TWindow::Paint" not in paint
    assert "uWindow.pas::TWindow::Reset" not in reset


def test_an_interface_method_dispatches_to_its_implementation(tmp_path: Path) -> None:
    (tmp_path / "uModel.pas").write_text(
        "unit uModel;\ninterface\ntype\n"
        "  IModel = interface\n    procedure Refresh;\n  end;\n"
        "  TModel = class(TInterfacedObject, IModel)\n  public\n    procedure Refresh;\n  end;\n"
        "implementation\nprocedure TModel.Refresh;\nbegin\nend;\nend.\n",
        encoding="utf-8",
    )
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()

    edge = graph.get_edge_data("uModel.pas::IModel::Refresh", "uModel.pas::TModel::Refresh")
    assert edge is not None and edge["edge_type"] == "dispatches_to"


def test_a_helper_method_answers_for_its_extended_types(tmp_path: Path) -> None:
    (tmp_path / "uHelpers.pas").write_text(
        "unit uHelpers;\ninterface\ntype\n"
        "  TBox = class\n  end;\n"
        "  TBoxHelper = class helper for TBox\n    procedure Shake;\n  end;\n"
        "  TTextHelper = record helper for string\n    function Shout: string;\n  end;\n"
        "implementation\n"
        "procedure TBoxHelper.Shake;\nbegin\nend;\n"
        "function TTextHelper.Shout: string;\nbegin\n  Result := Self;\nend;\n"
        "end.\n",
        encoding="utf-8",
    )
    (tmp_path / "uUse.pas").write_text(
        "unit uUse;\ninterface\nuses\n  uHelpers;\nprocedure Run(ABox: TBox);\n"
        "implementation\nprocedure Run(ABox: TBox);\nvar\n  S: string;\nbegin\n"
        "  ABox.Shake;\n  S.Shout;\nend;\nend.\n",
        encoding="utf-8",
    )
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()

    assert _callees(graph, "uUse.pas::Run") >= {
        "uHelpers.pas::TBoxHelper::Shake",
        "uHelpers.pas::TTextHelper::Shout",
    }


def test_a_property_read_and_write_reach_the_property_and_its_accessors(graph) -> None:
    assert "uMenus.pas::TMenus::StubText" in _callees(graph, "uWindow.pas::TWindow::Reset")
    assert _callees(graph, "uMenus.pas::TMenus::StubText") == {
        "uMenus.pas::TMenus::GetStubText",
        "uMenus.pas::TMenus::SetStubText",
    }
