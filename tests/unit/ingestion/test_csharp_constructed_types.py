"""``new R(..)`` of a C# type in another namespace, reached with no import tier.

C# sees a bare type name in the caller's namespace and the ones around it, then
through the file's ``using`` directives, every ``global using`` in the project
and the project's MSBuild ``<Using>`` items. Before this a constructor with no
``using`` behind it got no edge at all: the class and its constructor are two
symbols of one name, so the repo-wide tier read the name as ambiguous.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

R_IN_AB = "namespace A.B;\npublic class R\n{\n    public R(int n) { }\n}\n"
R_IN_AD = "namespace A.D;\npublic class R\n{\n    public R(int n) { }\n}\n"
CALLER = "namespace A;\nclass C\n{\n    void M()\n    {\n        var r = new R(1);\n    }\n}\n"
USES_AB = '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n    <Using Include="A.B" />\n  </ItemGroup>\n</Project>\n'


def _calls_from_m(
    tmp_path: Path, files: dict[str, str], caller: str = "C.cs::C::M"
) -> set[tuple[str, str]]:
    """``(callee, origin)`` of every call edge *caller* gets in a graph of *files*."""
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    return {
        (callee, data.get("resolution_origin"))
        for _, callee, data in graph.out_edges(caller, data=True)
        if data.get("edge_type") == "calls"
    }


class TestOneVisibleType:
    def test_a_parent_namespace_sees_its_child_s_type_unique_in_the_repo(
        self, tmp_path: Path
    ) -> None:
        """The issue's repro: no ``using``, one ``R`` in the repository."""
        assert _calls_from_m(tmp_path, {"R.cs": R_IN_AB, "C.cs": CALLER}) == {
            ("R.cs::R::R", "global_unique")
        }

    def test_the_caller_s_own_namespace(self, tmp_path: Path) -> None:
        files = {"R.cs": R_IN_AB.replace("A.B", "A"), "R2.cs": R_IN_AD, "C.cs": CALLER}
        assert _calls_from_m(tmp_path, files) == {("R.cs::R::R", "same_package")}

    def test_a_namespace_around_the_caller_s(self, tmp_path: Path) -> None:
        files = {
            "R.cs": R_IN_AB.replace("A.B", "A"),
            "R2.cs": R_IN_AD,
            "C.cs": CALLER.replace("namespace A;", "namespace A.X.Y;"),
        }
        assert _calls_from_m(tmp_path, files) == {("R.cs::R::R", "same_package")}

    def test_a_csproj_using_item(self, tmp_path: Path) -> None:
        files = {"P.csproj": USES_AB, "R.cs": R_IN_AB, "R2.cs": R_IN_AD, "C.cs": CALLER}
        assert _calls_from_m(tmp_path, files) == {("R.cs::R::R", "import_merged")}

    def test_a_global_using_in_another_file_of_the_project(self, tmp_path: Path) -> None:
        files = {
            "P.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
            "Usings.cs": "global using A.B;\n",
            "R.cs": R_IN_AB,
            "R2.cs": R_IN_AD,
            "C.cs": CALLER,
        }
        assert _calls_from_m(tmp_path, files) == {("R.cs::R::R", "import_merged")}

    @pytest.mark.parametrize(
        ("value", "expected"), [("true", {("R.cs::R::R", "import_merged")}), ("false", set())]
    )
    def test_a_directory_build_using_under_a_property_condition(
        self, tmp_path: Path, value: str, expected: set[tuple[str, str]]
    ) -> None:
        files = {
            "Directory.Build.targets": (
                "<Project>\n  <ItemGroup Condition=\"'$(IncludeAb)' == 'true'\">\n"
                '    <Using Include="A.B" />\n  </ItemGroup>\n</Project>\n'
            ),
            "src/P.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                f"    <IncludeAb>{value}</IncludeAb>\n  </PropertyGroup>\n</Project>\n"
            ),
            "R.cs": R_IN_AB,
            "R2.cs": R_IN_AD,
            "src/C.cs": CALLER,
        }
        assert _calls_from_m(tmp_path, files, "src/C.cs::C::M") == expected

    def test_constructors_split_across_partial_fragments_answer_with_the_type(
        self, tmp_path: Path
    ) -> None:
        """Overloads are chosen within one file, so two fragments' constructors are left alone."""
        part = "namespace A.B;\npublic partial class R\n{{\n    public R({0} n) {{ }}\n}}\n"
        files = {"R1.cs": part.format("int"), "R2.cs": part.format("string"), "C.cs": CALLER}
        calls = _calls_from_m(tmp_path, files)
        assert calls in ({("R1.cs::R", "global_unique")}, {("R2.cs::R", "global_unique")})

    @pytest.mark.parametrize(
        ("written", "expected"), [("new R(1)", "R.cs::R::R"), ("new R<int>(1)", "R.cs::R`1::R")]
    )
    def test_the_type_arguments_written_pick_plain_or_generic(
        self, tmp_path: Path, written: str, expected: str
    ) -> None:
        both = (
            "namespace A.B;\npublic class R\n{\n    public R(int n) { }\n}\n"
            "public class R<T>\n{\n    public R(T n) { }\n}\n"
        )
        files = {"R.cs": both, "C.cs": CALLER.replace("new R(1)", written)}
        assert _calls_from_m(tmp_path, files) == {(expected, "global_unique")}


class TestRefusals:
    def test_two_types_of_the_name_and_no_using(self, tmp_path: Path) -> None:
        files = {"R.cs": R_IN_AB, "R2.cs": R_IN_AD, "C.cs": CALLER}
        assert _calls_from_m(tmp_path, files) == set()

    def test_two_types_the_project_usings_bring_in(self, tmp_path: Path) -> None:
        files = {
            "P.csproj": USES_AB.replace("</ItemGroup>", '  <Using Include="A.D" />\n  </ItemGroup>'),
            "R.cs": R_IN_AB,
            "R2.cs": R_IN_AD,
            "C.cs": CALLER,
        }
        assert _calls_from_m(tmp_path, files) == set()

    def test_a_bare_call_that_is_not_a_construction(self, tmp_path: Path) -> None:
        """``R(1)`` with no ``new`` is a local function or a ``using static`` method."""
        files = {"R.cs": R_IN_AB, "C.cs": CALLER.replace("var r = new R(1);", "R(1);")}
        assert _calls_from_m(tmp_path, files) == set()

    def test_a_nested_type_is_not_visible_by_its_bare_name(self, tmp_path: Path) -> None:
        nested = (
            "namespace A.B;\npublic class Outer\n{\n"
            "    public class R\n    {\n        public R(int n) { }\n    }\n}\n"
        )
        assert _calls_from_m(tmp_path, {"Outer.cs": nested, "C.cs": CALLER}) == set()
