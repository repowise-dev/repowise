"""Behaviour pins for the class-aware same-file tier.

``_file_symbols`` is ``{file: {name: id}}`` — flat and last-wins — so a bare
``foo()`` inside class ``A`` bound to class ``B``'s ``foo`` whenever ``B`` came
later in the same file. ``_file_methods`` already carries the class, so the fix
is a lookup, not new data.

What makes it safe is the three shapes it must NOT claim, each of which reads
like the same defect and is not:

* a language without an implicit receiver, where a bare call is a module-level
  function and the caller's class is the wrong answer;
* a call site that is really ``obj.foo()``, which the grammar's bare-call
  pattern also matches (the parser keeps only the copy with the receiver);
* a flat hit that is a top-level function or a constructor rather than a rival
  class's method.

Each is pinned below, because each one silently costs correct edges.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        out[rel] = parse_file(_file_info(rel, abs_, lang), content.encode("utf-8"))
    return out


def _edges(parsed: dict[str, ParsedFile], tmp_path: Path) -> list[tuple[str, str, float, str]]:
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path))
    return [
        (rc.caller_id, rc.callee_id, rc.confidence, rc.origin)
        for path, pf in parsed.items()
        for rc in resolver.resolve_file(path, pf.calls)
    ]


def _targets_of(edges: list[tuple[str, str, float, str]], caller_suffix: str) -> list[str]:
    return [e[1] for e in edges if e[0].endswith(caller_suffix)]


# `B` deliberately comes second: last-wins is what puts `B::helper` in the flat
# index under the bare name `helper`.
JAVA_TWO_CLASSES = """
class A {
    int helper() { return 1; }
    int run() { return helper(); }
}

class B {
    int helper() { return 2; }
}
"""

JAVA_RECURSIVE = """
class A {
    int helper() { return helper(); }
}

class B {
    int helper() { return 2; }
}
"""

# `b.helper()` matches both the receiver-bearing pattern and the bare-call
# pattern; only the copy with the receiver may reach the resolver.
JAVA_MEMBER_SHAPED = """
class A {
    int helper() { return 1; }
    int run(B b) { return b.helper(); }
}

class B {
    int helper() { return 2; }
}
"""

PYTHON_TWO_CLASSES = '''
class A:
    def helper(self):
        return 1

    def run(self):
        return helper()


class B:
    def helper(self):
        return 2
'''

KOTLIN_TOP_LEVEL_FN = """
class A {
    fun helper(): Int = 2
    fun run(): Int = helper()
}

fun helper(): Int = 1
"""

JAVA_CONSTRUCTOR = """
class Holder {
    static class Entry { }
    Object make() { return new Entry(); }
}

class Entry {
    Entry() { }
}
"""


class TestClassAwareTier:
    def test_bare_call_binds_to_the_callers_own_class(self, tmp_path: Path) -> None:
        parsed = _parse_all(tmp_path, {"src/A.java": ("java", JAVA_TWO_CLASSES)})
        edges = _edges(parsed, tmp_path)
        hits = [e for e in edges if e[0].endswith("::A::run")]
        assert [e[1].split("::")[-2:] for e in hits] == [["A", "helper"]], (
            f"helper() inside A.run must reach A::helper, not B's; edges: {edges}"
        )
        assert hits[0][2:] == (0.95, "enclosing_class")

    def test_recursion_does_not_reach_another_class(self, tmp_path: Path) -> None:
        """The caller's own class's `helper` IS the caller — that is a self-call."""
        parsed = _parse_all(tmp_path, {"src/A.java": ("java", JAVA_RECURSIVE)})
        edges = _edges(parsed, tmp_path)
        assert _targets_of(edges, "::A::helper") == [], (
            f"recursive helper() must not edge to B::helper; edges: {edges}"
        )

    def test_a_member_shaped_site_is_not_treated_as_bare(self, tmp_path: Path) -> None:
        parsed = _parse_all(tmp_path, {"src/A.java": ("java", JAVA_MEMBER_SHAPED)})
        edges = _edges(parsed, tmp_path)
        assert not [t for t in _targets_of(edges, "::A::run") if t.endswith("::A::helper")], (
            f"b.helper() must not resolve to the caller's own helper; edges: {edges}"
        )

    def test_language_without_an_implicit_receiver_is_untouched(self, tmp_path: Path) -> None:
        """A bare `helper()` in Python is a module-level function, never `self.helper()`."""
        parsed = _parse_all(tmp_path, {"src/a.py": ("python", PYTHON_TWO_CLASSES)})
        edges = _edges(parsed, tmp_path)
        assert not [t for t in _targets_of(edges, "::A::run") if t.endswith("::A::helper")], (
            f"Python bare call must not bind to the caller's class; edges: {edges}"
        )

    def test_a_top_level_function_hit_is_left_alone(self, tmp_path: Path) -> None:
        """Kotlin puts free functions beside classes, so the flat hit may be right.

        Which of the two Kotlin would pick depends on the signatures, which the
        `(class, method)` index does not carry — so the tier declines rather
        than guessing.
        """
        parsed = _parse_all(tmp_path, {"src/A.kt": ("kotlin", KOTLIN_TOP_LEVEL_FN)})
        edges = _edges(parsed, tmp_path)
        targets = _targets_of(edges, "::A::run")
        assert targets and not any(t.endswith("::A::helper") for t in targets), (
            f"the top-level helper must keep the edge; edges: {edges}"
        )

    def test_a_constructor_hit_is_left_alone(self, tmp_path: Path) -> None:
        """`new Entry()` reaches a constructor; a same-named nested type is not a rival."""
        parsed = _parse_all(tmp_path, {"src/Holder.java": ("java", JAVA_CONSTRUCTOR)})
        edges = _edges(parsed, tmp_path)
        assert not [t for t in _targets_of(edges, "::make") if t.endswith("::Holder::Entry")], (
            f"a constructor hit must not be redirected; edges: {edges}"
        )


# jsoup's shape: `Tag` puts `set` in the file's flat index, so a bare-name
# reading of `data.set(str)` has a wrong answer to give.
JAVA_TOKEN_DATA = "package p;\n\nclass TokenData {\n    void set(String s) { }\n}\n"
JAVA_TOKEN = """package p;

class Character {
    private final TokenData data = new TokenData();

    void data(String str) {
        data.set(str);
    }
}

class Tag {
    Tag set(int n) { return this; }
}
"""

# `o` is typed by nothing the resolver can read, so the call keeps the bare-name
# answer the grammar's bare pattern always gave it.
JAVA_UNTYPED_RECEIVER = """
class A {
    <T> int count(T o) { return o.size(); }
}

class Box {
    int size() { return 0; }
}
"""

JAVA_SAME_LINE = """
class A {
    int helper(int x) { return x; }
    int run(B b) { return helper(b.helper(1)); }
}

class B {
    int helper(int x) { return x; }
}
"""

RUBY_POOL = """
class Pool
  def drain(conn)
    close(conn)
  end

  def close(x)
  end
end
"""


class TestReceiverlessTwin:
    """``obj.m()`` reaches the resolver once, with its receiver."""

    def test_a_typed_member_call_gets_no_bare_name_edge(self, tmp_path: Path) -> None:
        parsed = _parse_all(
            tmp_path,
            {
                "p/TokenData.java": ("java", JAVA_TOKEN_DATA),
                "p/Token.java": ("java", JAVA_TOKEN),
            },
        )
        edges = _edges(parsed, tmp_path)
        assert _targets_of(edges, "::Character::data") == ["p/TokenData.java::TokenData::set"], (
            f"data.set(str) must reach TokenData only; edges: {edges}"
        )

    def test_an_untyped_receiver_falls_back_to_the_bare_name(self, tmp_path: Path) -> None:
        parsed = _parse_all(tmp_path, {"src/A.java": ("java", JAVA_UNTYPED_RECEIVER)})
        edges = _edges(parsed, tmp_path)
        assert ("src/A.java::A::count", "src/A.java::Box::size", 0.95, "same_file") in edges, (
            f"o.size() must keep its bare-name edge; edges: {edges}"
        )

    def test_a_bare_call_on_its_twins_line_still_resolves(self, tmp_path: Path) -> None:
        """``helper(b.helper(1))``: the outer call is a genuine implicit receiver."""
        parsed = _parse_all(tmp_path, {"src/A.java": ("java", JAVA_SAME_LINE)})
        edges = _edges(parsed, tmp_path)
        assert "src/A.java::A::helper" in _targets_of(edges, "::A::run"), (
            f"the outer helper() must reach A::helper; edges: {edges}"
        )

    def test_a_ruby_bare_call_still_resolves(self, tmp_path: Path) -> None:
        parsed = _parse_all(tmp_path, {"lib/pool.rb": ("ruby", RUBY_POOL)})
        edges = _edges(parsed, tmp_path)
        assert _targets_of(edges, "::drain") == ["lib/pool.rb::Pool::close"], (
            f"close(conn) must reach Pool::close; edges: {edges}"
        )
