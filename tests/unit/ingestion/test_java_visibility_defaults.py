"""A Java declaration with no access keyword takes its enclosing scope's default.

No access keyword means package-private, recorded ``internal``, except that
interface and annotation members are implicitly public and an enum
constructor is implicitly private. ``refine_java_visibility`` reads the
declaration's own ``modifiers`` node and its enclosing body.
"""

from __future__ import annotations

from datetime import datetime

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _symbols(src: str, path: str = "src/p/Thing.java") -> list[tuple[str, str, str]]:
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="java",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    parsed = _PARSER.parse_file(info, src.encode("utf-8"))
    return [(s.kind, s.name, s.visibility) for s in parsed.symbols]


def _visibility(src: str) -> dict[str, str]:
    return {name: vis for _, name, vis in _symbols(src)}


def test_unmodified_top_level_types_are_package_private() -> None:
    src = """
package p;
class Helper {}
interface Port {}
enum Mode { A, B }
record Pair(int a, int b) {}
public class Api {}
"""
    syms = _symbols(src)
    assert ("class", "Helper", "internal") in syms
    assert ("interface", "Port", "internal") in syms
    assert ("enum", "Mode", "internal") in syms
    assert ("class", "Pair", "internal") in syms
    assert ("class", "Api", "public") in syms


def test_unmodified_class_members_are_package_private() -> None:
    src = """
public class Outer {
    static class Deserializer {}
    void run() {}
    static final void helper() {}
    @Override void annotated() {}
}
"""
    vis = _visibility(src)
    assert vis["Deserializer"] == "internal"
    assert vis["run"] == "internal"
    assert vis["helper"] == "internal"
    assert vis["annotated"] == "internal"


def test_explicit_access_keywords_win() -> None:
    src = """
public class Outer {
    @Deprecated private static class Hidden {}
    private class Inner { public void open() {} }
    protected void guarded() {}
    public void exposed() {}
}
"""
    vis = _visibility(src)
    # The modifier-less query pattern matches first and used to drop the
    # captured ``private`` of a class declaration.
    assert vis["Hidden"] == "private"
    assert vis["Inner"] == "private"
    assert vis["open"] == "public"
    assert vis["guarded"] == "protected"
    assert vis["exposed"] == "public"


def test_interface_and_annotation_members_are_public() -> None:
    src = """
interface Repo {
    void save();
    default void flush() {}
    static void create() {}
    class Nested {}
    private void secret() {}
}
@interface Marker {
    class Holder {}
}
"""
    vis = _visibility(src)
    assert vis["save"] == "public"
    assert vis["flush"] == "public"
    assert vis["create"] == "public"
    assert vis["Nested"] == "public"
    assert vis["secret"] == "private"
    assert vis["Holder"] == "public"


def test_enum_constructor_is_private_and_enum_methods_package_private() -> None:
    src = """
public enum Color {
    RED, GREEN;
    void paint() {}
    Color() {}
}
"""
    syms = _symbols(src)
    assert ("enum", "Color", "public") in syms
    assert ("method", "Color", "private") in syms
    assert ("method", "paint", "internal") in syms


def test_record_body_is_package_private_and_accessors_stay_public() -> None:
    src = """
record Point(int x, int y) {
    void shift() {}
}
"""
    vis = _visibility(src)
    assert vis["shift"] == "internal"
    assert vis["x"] == "public"
    assert vis["y"] == "public"
