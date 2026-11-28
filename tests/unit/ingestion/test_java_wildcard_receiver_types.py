"""A call on a type that a wildcard import brings in reaches the method of its parent."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file

_API = {
    "a/api/Left.java": "package a.api;\npublic interface Left { }\n",
    "a/api/Right.java": "package a.api;\npublic interface Right { void send(String text); }\n",
    "a/api/Both.java": (
        "package a.api;\npublic interface Both extends Left, Right { String n(); }\n"
    ),
    "a/api/Impl.java": (
        "package a.api;\n"
        "public class Impl implements Both {\n"
        "  public void send(String text) { }\n"
        "  public String n() { return null; }\n"
        "}\n"
    ),
}


def _resolve(
    tmp_path: Path, files: dict[str, str], caller: str, heritage: dict[str, set[str]] | None = None
) -> set[str]:
    """The callees the resolver finds in the file *caller* of *files*."""
    parsed: dict[str, ParsedFile] = {}
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        info = FileInfo(
            path=rel,
            abs_path=str(path),
            language=cast(Any, "java"),
            size_bytes=path.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parse_file(info, content.encode("utf-8"))
    resolver = CallResolver(
        parsed, {p: set() for p in parsed}, repo_path=str(tmp_path), heritage_parents=heritage or {}
    )
    return {rc.callee_id for rc in resolver.resolve_file(caller, parsed[caller].calls)}


def _callees(tmp_path: Path, imports: str) -> set[str]:
    files = {
        **_API,
        "a/use/User.java": (
            f"package a.use;\n{imports}public class User {{\n"
            '  public void go(Both both) {\n    both.send("x");\n  }\n}\n'
        ),
    }
    heritage = {
        "a/api/Both.java::Both": {"a/api/Left.java::Left", "a/api/Right.java::Right"},
        "a/api/Impl.java::Impl": {"a/api/Both.java::Both"},
    }
    return _resolve(tmp_path, files, "a/use/User.java", heritage)


def test_a_call_on_a_type_imported_by_a_wildcard_reaches_the_method_of_its_parent(
    tmp_path: Path,
) -> None:
    assert "a/api/Right.java::Right::send" in _callees(tmp_path, "import a.api.*;\n")


def test_a_wildcard_import_of_another_package_leaves_the_call_unresolved(tmp_path: Path) -> None:
    assert "a/api/Right.java::Right::send" not in _callees(tmp_path, "import b.other.*;\n")


def test_a_type_of_the_package_shadows_a_type_of_a_wildcard_import(tmp_path: Path) -> None:
    outer = "public class Outer { public static class Inner { public static void m() { } } }\n"
    files = {
        "a/use/Outer.java": f"package a.use;\n{outer}",
        "a/api/Outer.java": f"package a.api;\n{outer}",
        "a/use/User.java": (
            "package a.use;\nimport a.api.*;\n"
            "public class User {\n  public void go() {\n    Outer.Inner.m();\n  }\n}\n"
        ),
    }
    assert _resolve(tmp_path, files, "a/use/User.java") == {"a/use/Outer.java::Inner::m"}
