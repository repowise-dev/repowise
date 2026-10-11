"""Tests for C# dynamic-import markers in dead-code analysis.

Files containing reflection / DI registration / InternalsVisibleTo are
recorded in ``DeadCodeAnalyzer._dynamic_import_files`` so the analyser
can lower confidence on dead-code candidates that may actually be
loaded at runtime by the framework.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion.models import FileInfo, ParsedFile


def _make_parsed(path: str, abs_path: str) -> ParsedFile:
    fi = FileInfo(
        path=path,
        abs_path=abs_path,
        language="csharp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ParsedFile(
        file_info=fi,
        symbols=[],
        imports=[],
        exports=[],
    )


def test_activator_create_instance_marks_file(tmp_path: Path) -> None:
    src = tmp_path / "Loader.cs"
    src.write_text(
        "public class Loader { void Go() { Activator.CreateInstance(typeof(Plugin)); } }\n"
    )
    parsed = {"Loader.cs": _make_parsed("Loader.cs", str(src))}
    analyzer = DeadCodeAnalyzer(graph=nx.DiGraph(), parsed_files=parsed)
    assert "Loader.cs" in analyzer._dynamic_import_files


def test_di_registration_marks_file(tmp_path: Path) -> None:
    src = tmp_path / "Program.cs"
    src.write_text(
        "var b = WebApplication.CreateBuilder(args);\n"
        "b.Services.AddScoped<IUserService, UserService>();\n"
    )
    parsed = {"Program.cs": _make_parsed("Program.cs", str(src))}
    analyzer = DeadCodeAnalyzer(graph=nx.DiGraph(), parsed_files=parsed)
    assert "Program.cs" in analyzer._dynamic_import_files


def test_internals_visible_to_marks_file(tmp_path: Path) -> None:
    src = tmp_path / "AssemblyInfo.cs"
    src.write_text('[assembly: InternalsVisibleTo("Acme.Tests")]\n')
    parsed = {"AssemblyInfo.cs": _make_parsed("AssemblyInfo.cs", str(src))}
    analyzer = DeadCodeAnalyzer(graph=nx.DiGraph(), parsed_files=parsed)
    assert "AssemblyInfo.cs" in analyzer._dynamic_import_files


def test_type_gettype_marks_file(tmp_path: Path) -> None:
    src = tmp_path / "Boot.cs"
    src.write_text('var t = Type.GetType("Acme.Worker");\n')
    parsed = {"Boot.cs": _make_parsed("Boot.cs", str(src))}
    analyzer = DeadCodeAnalyzer(graph=nx.DiGraph(), parsed_files=parsed)
    assert "Boot.cs" in analyzer._dynamic_import_files


def test_plain_csharp_file_not_marked(tmp_path: Path) -> None:
    src = tmp_path / "Plain.cs"
    src.write_text("public class Plain { public int X => 1; }\n")
    parsed = {"Plain.cs": _make_parsed("Plain.cs", str(src))}
    analyzer = DeadCodeAnalyzer(graph=nx.DiGraph(), parsed_files=parsed)
    assert "Plain.cs" not in analyzer._dynamic_import_files
