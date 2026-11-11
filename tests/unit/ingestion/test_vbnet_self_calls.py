"""VB.NET spells its self-reference ``Me`` (and ``MyClass``), not ``this``."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _calls(graph, caller: str) -> dict[str, str]:
    """``{callee id: resolution origin}`` for every call edge out of *caller*."""
    return {
        callee: data.get("resolution_origin")
        for _, callee, data in graph.out_edges(caller, data=True)
        if data.get("edge_type") == "calls"
    }


def _form(receiver: str) -> str:
    return (
        "Public Class MainForm\n"
        "    Private Sub Load()\n"
        f"        {receiver}.Wire()\n"
        "    End Sub\n"
        "    Private Sub Wire()\n"
        "    End Sub\n"
        "End Class\n"
    )


def test_me_call_resolves_in_the_same_class(tmp_path: Path) -> None:
    (tmp_path / "MainForm.vb").write_text(_form("Me"))

    assert _calls(_build(tmp_path), "MainForm.vb::MainForm::Load") == {
        "MainForm.vb::MainForm::Wire": "self_scope"
    }


def test_myclass_call_resolves_in_the_same_class(tmp_path: Path) -> None:
    (tmp_path / "MainForm.vb").write_text(_form("MyClass"))

    assert _calls(_build(tmp_path), "MainForm.vb::MainForm::Load") == {
        "MainForm.vb::MainForm::Wire": "self_scope"
    }


def test_me_is_a_keyword_in_any_case(tmp_path: Path) -> None:
    # VB.NET is case-insensitive, so `me.Wire()` is the same call.
    (tmp_path / "MainForm.vb").write_text(_form("me"))

    assert _calls(_build(tmp_path), "MainForm.vb::MainForm::Load") == {
        "MainForm.vb::MainForm::Wire": "self_scope"
    }


def test_me_call_reaches_a_sibling_partial_fragment(tmp_path: Path) -> None:
    (tmp_path / "MainForm.vb").write_text(
        "Partial Public Class MainForm\n"
        "    Private Sub Load()\n"
        "        Me.Wire()\n"
        "    End Sub\n"
        "End Class\n"
    )
    (tmp_path / "MainForm.Designer.vb").write_text(
        "Partial Public Class MainForm\n    Private Sub Wire()\n    End Sub\nEnd Class\n"
    )

    assert _calls(_build(tmp_path), "MainForm.vb::MainForm::Load") == {
        "MainForm.Designer.vb::MainForm::Wire": "self_scope"
    }


def test_a_csharp_receiver_named_me_is_not_a_self_call(tmp_path: Path) -> None:
    # `Me` is only a keyword in VB.NET. In C# it is an ordinary identifier, and
    # a call on it must not be read as a call on the enclosing class.
    (tmp_path / "Form.cs").write_text(
        "class Peer { public void Wire() {} }\n"
        "class Form\n{\n"
        "    void Load(Peer Me)\n    {\n        Me.Wire();\n    }\n"
        "    void Wire() {}\n"
        "}\n"
    )

    assert "Form.cs::Form::Wire" not in _calls(_build(tmp_path), "Form.cs::Form::Load")
