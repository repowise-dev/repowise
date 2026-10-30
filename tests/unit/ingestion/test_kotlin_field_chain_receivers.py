"""A Kotlin call on a field path (``a.b.m()``, ``a.b!!.c?.m()``) walks each field's declared type."""

from __future__ import annotations

from pathlib import Path

from tests.unit.ingestion.test_call_resolver_strategies import _edges, _parse_all

_FILES = {
    "a/Segment.kt": (
        "package a\n\nclass Segment {\n    var prev: Segment? = null\n"
        "    fun push(s: Segment): Segment = s\n    fun pop(): Segment? = null\n}\n"
    ),
    "a/Buffer.kt": "package a\n\nclass Buffer {\n    internal var head: Segment? = null\n    fun clear() {}\n}\n",
    "a/Source.kt": "package a\n\nclass Source(val buffer: Buffer)\n",
}


def _targets(tmp_path: Path, body: str) -> list[tuple[str, str]]:
    use = (
        "package a\n\nclass Use(private val src: Source) {\n"
        f"    fun f(out: Buffer, copy: Segment, next: Segment) {{\n        {body}\n    }}\n}}\n"
    )
    files = {**_FILES, "a/Use.kt": use}
    parsed = _parse_all(tmp_path, {k: ("kotlin", v) for k, v in files.items()})
    return sorted((callee, origin) for _, callee, _, origin in _edges(parsed, tmp_path))


def test_a_field_path_on_a_property_reaches_the_last_fields_class(tmp_path: Path) -> None:
    assert _targets(tmp_path, "src.buffer.clear()") == [
        ("a/Buffer.kt::Buffer::clear", "receiver_chain_import")
    ]


def test_not_null_and_safe_call_hops_name_the_same_fields(tmp_path: Path) -> None:
    assert _targets(tmp_path, "out.head!!.prev!!.push(copy)") == [
        ("a/Segment.kt::Segment::push", "receiver_chain_import")
    ]
    assert _targets(tmp_path, "next.prev?.pop()") == [
        ("a/Segment.kt::Segment::pop", "receiver_chain_import")
    ]


def test_a_path_through_this_starts_at_the_callers_class(tmp_path: Path) -> None:
    assert _targets(tmp_path, "this.src.buffer.clear()") == [
        ("a/Buffer.kt::Buffer::clear", "receiver_chain_import")
    ]


def test_an_untyped_hop_refuses_the_chain(tmp_path: Path) -> None:
    assert _targets(tmp_path, "src.missing.clear()") == []


def test_a_call_inside_the_path_mints_no_site(tmp_path: Path) -> None:
    use = "package a\n\nfun f(b: Buffer) {\n    b.list()[0].clear()\n    b::class.java.clear()\n}\n"
    parsed = _parse_all(tmp_path, {"a/Use.kt": ("kotlin", use)})
    assert [c.receiver_name for c in parsed["a/Use.kt"].calls if c.target_name == "clear"] == []


def test_package_and_nested_type_paths_bind_nothing_new(tmp_path: Path) -> None:
    files = {
        "a/Outer.kt": "package a\n\nclass Outer {\n    class Inner {\n        fun clear() {}\n    }\n}\n",
        "a/Use.kt": "package a\n\nfun f() {\n    kotlin.io.clear()\n    a.Outer.clear()\n}\n",
    }
    parsed = _parse_all(tmp_path, {k: ("kotlin", v) for k, v in files.items()})
    assert _edges(parsed, tmp_path) == []
