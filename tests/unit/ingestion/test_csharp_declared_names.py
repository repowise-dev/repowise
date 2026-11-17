"""``is``, ``as`` and ``instanceof`` type tests declare no name of their own."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file

CSHARP = """namespace A;
public class D {
    public void Run(object o) {
        if (o is Engine) { }
        var m = o as Motor;
        var ok = (o as Wheel)?.Spin();
    }
}
"""

JAVA = """class D {
    void run(Object o) {
        if (o instanceof Engine) { }
    }
}
"""


def _declared(tmp_path: Path, path: str, language: str, source: str, owner: str) -> frozenset[str]:
    abs_ = tmp_path / path
    abs_.parent.mkdir(parents=True, exist_ok=True)
    abs_.write_text(source)
    info = FileInfo(
        path=path,
        abs_path=str(abs_),
        language=language,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = {path: parse_file(info, source.encode("utf-8"))}
    resolver = CallResolver(parsed, {path: set()}, repo_path=str(tmp_path))
    return resolver._names_declared_by(path, owner)


def test_is_and_as_tests_declare_no_name(tmp_path: Path) -> None:
    assert _declared(tmp_path, "D.cs", "csharp", CSHARP, "D.cs::D::Run") == frozenset(
        {"m", "o", "ok"}
    )


def test_a_pattern_variable_after_is_still_declared(tmp_path: Path) -> None:
    source = CSHARP.replace("if (o is Engine) { }", "if (o is Engine e) { e.Run(); }")
    assert "e" in _declared(tmp_path, "D.cs", "csharp", source, "D.cs::D::Run")


def test_java_instanceof_declares_no_name(tmp_path: Path) -> None:
    assert _declared(tmp_path, "D.java", "java", JAVA, "D.java::D::run") == frozenset({"o"})
