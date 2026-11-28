"""Delphi and Lazarus form files name the event handlers of their unit's form class.

The end-to-end cases go through traverse -> parse -> graph -> framework edges,
the path a real index takes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.framework_edges.pascal_forms import form_handlers

_UNIT = (
    "unit uMain;\n"
    "interface\n"
    "uses Vcl.Forms, Vcl.StdCtrls;\n"
    "type\n"
    "  TMainForm = class(TForm)\n"
    "    Button1: TButton;\n"
    "    procedure Button1Click(Sender: TObject);\n"
    "  private\n"
    "    procedure SaveClick(Sender: TObject);\n"
    "    procedure Unbound;\n"
    "  end;\n"
    "var MainForm: TMainForm;\n"
    "implementation\n"
    "{$R *.dfm}\n"
    "procedure TMainForm.Button1Click(Sender: TObject);\nbegin\nend;\n"
    "procedure TMainForm.SaveClick(Sender: TObject);\nbegin\nend;\n"
    "procedure TMainForm.Unbound;\nbegin\nend;\n"
    "end.\n"
)

_FORM = (
    "object MainForm: TMainForm\n"
    "  Caption = 'Demo'\n"
    "  OnCreate = nil\n"
    "  object Button1: TButton\n"
    "    OnClick = Button1Click\n"
    "  end\n"
    "  object Memo1: TMemo\n"
    "    Lines.Strings = (\n"
    "      'OnClick = Fake'\n"
    "      'x')\n"
    "  end\n"
    "  object Button2: TButton\n"
    "    onclick = SAVECLICK\n"
    "  end\n"
    "end\n"
)


def _build(repo: Path, files: dict[str, str | bytes]) -> object:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges([])
    return builder.graph()


def _bound(graph: object, form: str = "uMain.dfm") -> set[str]:
    return {
        target
        for _source, target, data in graph.out_edges(f"{form}::__module__", data=True)
        if data.get("edge_type") == "framework_binds"
    }


def _framework_edges(graph: object, form: str) -> list[tuple[str, str]]:
    return [
        (target, data["edge_type"])
        for _source, target, data in graph.out_edges(form, data=True)
        if data["edge_type"].startswith("framework")
    ]


def test_form_handlers_reads_event_properties_and_nothing_else() -> None:
    assert form_handlers(_FORM) == ("TMainForm", ["Button1Click", "SAVECLICK"])


def test_form_handlers_of_a_binary_form_is_empty() -> None:
    assert form_handlers("TPF0\x0aTMainForm\x08MainForm") == (None, [])


def test_a_handler_named_in_the_form_is_bound_from_its_unit(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"uMain.pas": _UNIT, "uMain.dfm": _FORM})

    assert _bound(graph) == {
        "uMain.pas::TMainForm::Button1Click",
        "uMain.pas::TMainForm::SaveClick",
    }


def test_the_form_file_depends_on_its_unit_and_names_the_class_and_handlers(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"uMain.pas": _UNIT, "uMain.dfm": _FORM})

    edge = graph.get_edge_data("uMain.dfm", "uMain.pas")
    assert edge["edge_type"] == "framework"
    assert sorted(edge["imported_names"]) == ["Button1Click", "SaveClick", "TMainForm"]


@pytest.mark.parametrize("extension", [".fmx", ".lfm"])
def test_firemonkey_and_lazarus_forms_bind_like_delphi_forms(
    tmp_path: Path, extension: str
) -> None:
    graph = _build(tmp_path, {"uMain.pas": _UNIT, f"uMain{extension}": _FORM})

    assert graph.has_edge(f"uMain{extension}", "uMain.pas")
    assert "uMain.pas::TMainForm::Button1Click" in _bound(graph, f"uMain{extension}")


def test_a_form_with_the_unit_in_another_case_still_binds(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"uMain.pas": _UNIT, "UMAIN.DFM": _FORM})

    assert graph.has_edge("UMAIN.DFM", "uMain.pas")
    assert "uMain.pas::TMainForm::Button1Click" in _bound(graph, "UMAIN.DFM")


def test_a_form_without_a_unit_adds_no_edge(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"Lonely.dfm": _FORM, "uMain.pas": _UNIT})

    assert _framework_edges(graph, "Lonely.dfm") == []
    assert _bound(graph, "Lonely.dfm") == set()


def test_a_handler_the_unit_does_not_declare_is_left_out(tmp_path: Path) -> None:
    form = "object MainForm: TMainForm\n  OnClick = Ghost\n  OnShow = Button1Click\nend\n"
    graph = _build(tmp_path, {"uMain.pas": _UNIT, "uMain.dfm": form})

    assert graph.get_edge_data("uMain.dfm", "uMain.pas")["imported_names"] == [
        "TMainForm",
        "Button1Click",
    ]
    assert not graph.has_node("uMain.pas::TMainForm::Ghost")


def test_a_binary_form_is_indexed_without_edges(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"uMain.pas": _UNIT, "uMain.dfm": b"TPF0\x05TForm\x04Main\x00"})

    assert _framework_edges(graph, "uMain.dfm") == []
    assert _bound(graph) == set()
