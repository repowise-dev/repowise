"""Pascal ``uses`` resolve through the project files that compile each unit."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.graph import build_stem_map
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.pascal import resolve_pascal_import_all


def _ctx(root: Path, files: dict[str, str]) -> ResolverContext:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    indexed = {rel for rel in files if not rel.lower().endswith((".dproj", ".lpi"))}
    return ResolverContext(
        path_set=indexed,
        stem_map=build_stem_map(indexed),
        graph=nx.DiGraph(),
        repo_path=root,
    )


_UNIT = "unit X;\ninterface\nimplementation\nend.\n"


def test_a_dotted_unit_name_resolves_to_its_whole_file_stem(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {"src/App.Core.Utils.pas": _UNIT, "src/App.Core.pas": _UNIT, "src/Main.pas": _UNIT},
    )

    assert resolve_pascal_import_all("App.Core.Utils", "src/Main.pas", ctx) == (
        "src/App.Core.Utils.pas",
    )


def test_an_rtl_unit_does_not_land_on_a_repo_file_sharing_its_first_segment(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path, {"lib/System.pas": _UNIT, "lib/Vcl.pas": _UNIT, "src/Main.pas": _UNIT})

    assert resolve_pascal_import_all("System.SysUtils", "src/Main.pas", ctx) == ()
    assert resolve_pascal_import_all("Vcl.Forms", "src/Main.pas", ctx) == ()


def test_unit_names_match_case_insensitively(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, {"src/uToast.pas": _UNIT, "src/Main.pas": _UNIT})

    assert resolve_pascal_import_all("UTOAST", "src/Main.pas", ctx) == ("src/uToast.pas",)


def test_a_project_in_path_picks_the_unit_it_names(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {
            "src/App.dpr": (
                "program App;\nuses\n  uPty in 'Platform\\Windows\\uPty.pas',\n"
                "  uMain in 'Core\\uMain.pas';\nbegin\nend.\n"
            ),
            "src/Core/uMain.pas": _UNIT,
            "src/Platform/Windows/uPty.pas": _UNIT,
            "src/Platform/Posix/uPty.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("uPty", "src/App.dpr", ctx) == (
        "src/Platform/Windows/uPty.pas",
    )
    # The project's choice comes first: it is the import's resolved file.
    assert resolve_pascal_import_all("uPty", "src/Core/uMain.pas", ctx)[0] == (
        "src/Platform/Windows/uPty.pas"
    )


def test_shared_code_reaches_the_variant_of_every_project_that_compiles_it(
    tmp_path: Path,
) -> None:
    ctx = _ctx(
        tmp_path,
        {
            "src/Win.dpr": (
                "program Win;\nuses\n  uPty in 'Platform\\Windows\\uPty.pas',\n"
                "  uMain in 'Core\\uMain.pas';\nbegin\nend.\n"
            ),
            "src/Tui.dpr": (
                "program Tui;\nuses\n  uPty in 'Platform\\Posix\\uPty.pas',\n"
                "  uMain in 'Core\\uMain.pas';\nbegin\nend.\n"
            ),
            "src/Core/uMain.pas": _UNIT,
            "src/Platform/Windows/uPty.pas": _UNIT,
            "src/Platform/Posix/uPty.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("uPty", "src/Core/uMain.pas", ctx) == (
        "src/Platform/Posix/uPty.pas",
        "src/Platform/Windows/uPty.pas",
    )


def test_dproj_search_path_selects_the_platform_directory(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {
            "src/App.dpr": "program App;\nuses\n  uMain;\nbegin\nend.\n",
            "src/App.dproj": (
                "<Project><PropertyGroup>"
                "<DCC_UnitSearchPath>.\\Core;.\\Platform\\Windows;$(DCC_UnitSearchPath)"
                "</DCC_UnitSearchPath></PropertyGroup></Project>"
            ),
            "src/Core/uMain.pas": _UNIT,
            "src/Platform/Windows/uPty.pas": _UNIT,
            "src/Platform/Posix/uPty.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("uPty", "src/Core/uMain.pas", ctx)[0] == (
        "src/Platform/Windows/uPty.pas"
    )
    assert resolve_pascal_import_all("uMain", "src/App.dpr", ctx) == ("src/Core/uMain.pas",)


def test_a_variant_no_project_claims_stays_reachable(tmp_path: Path) -> None:
    # A build script outside the project files (dcc -U...) selects the Posix
    # directory, so the variant no project names is an alternative, not dead.
    ctx = _ctx(
        tmp_path,
        {
            "src/App.dpr": (
                "program App;\nuses\n  uPty in 'Platform\\Windows\\uPty.pas',\n"
                "  uMain in 'Core\\uMain.pas';\nbegin\nend.\n"
            ),
            "src/Core/uMain.pas": _UNIT,
            "src/Platform/Windows/uPty.pas": _UNIT,
            "src/Platform/Posix/uPty.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("uPty", "src/Core/uMain.pas", ctx) == (
        "src/Platform/Windows/uPty.pas",
        "src/Platform/Posix/uPty.pas",
    )


def test_dproj_unit_scope_names_complete_a_short_unit_name(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {
            "src/App.dpr": "program App;\nuses\n  Utils;\nbegin\nend.\n",
            "src/App.dproj": (
                "<Project><PropertyGroup><DCC_Namespace>System;MyCo.Lib;$(DCC_Namespace)"
                "</DCC_Namespace></PropertyGroup></Project>"
            ),
            "src/MyCo.Lib.Utils.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("Utils", "src/App.dpr", ctx) == ("src/MyCo.Lib.Utils.pas",)


def test_lazarus_project_other_unit_files_act_as_search_path(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {
            "app/App.lpr": "program App;\nuses\n  uMain;\nbegin\nend.\n",
            "app/App.lpi": (
                "<CONFIG><ProjectOptions/><CompilerOptions><SearchPaths>"
                '<OtherUnitFiles Value="core;unix"/></SearchPaths></CompilerOptions></CONFIG>'
            ),
            "app/core/uMain.pas": _UNIT,
            "app/unix/uPty.pas": _UNIT,
            "app/win/uPty.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("uPty", "app/core/uMain.pas", ctx)[0] == "app/unix/uPty.pas"


def test_a_commented_out_in_path_is_ignored(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {
            "src/App.dpr": (
                "program App;\nuses\n  { uPty in 'Posix\\uPty.pas', }\n"
                "  uPty in 'Windows\\uPty.pas';\nbegin\nend.\n"
            ),
            "src/Windows/uPty.pas": _UNIT,
            "src/Posix/uPty.pas": _UNIT,
        },
    )

    assert resolve_pascal_import_all("uPty", "src/App.dpr", ctx) == ("src/Windows/uPty.pas",)


def test_outside_any_project_the_same_directory_unit_wins(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {"a/uPty.pas": _UNIT, "a/uMain.pas": _UNIT, "b/uPty.pas": _UNIT},
    )

    assert resolve_pascal_import_all("uPty", "a/uMain.pas", ctx) == ("a/uPty.pas",)


def test_outside_any_project_ambiguous_variants_are_all_kept(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        {"win/uPty.pas": _UNIT, "posix/uPty.pas": _UNIT, "core/uMain.pas": _UNIT},
    )

    assert resolve_pascal_import_all("uPty", "core/uMain.pas", ctx) == (
        "posix/uPty.pas",
        "win/uPty.pas",
    )
