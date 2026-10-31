"""A Kotlin infix call ``a f b`` is the call ``a.f(b)``."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from tests.unit.ingestion.test_call_resolver_strategies import _edges, _parse_all

_SOURCE = """\
package a

internal infix fun Byte.and(other: Int): Int = toInt() and other
internal infix fun Byte.and(other: Long): Long = toLong() and other
internal infix fun Byte.shr(other: Int): Int = toInt() shr other

fun f(byte: Byte, x: Int) {
    val a = byte and 0xc0
    val b = byte and 0xffL
    val c = x and 3
    val d = byte shr 4 and 0xf
}
"""


def test_infix_calls_are_call_sites_with_one_argument(tmp_path: Path) -> None:
    parsed = _parse_all(tmp_path, {"a/Bits.kt": ("kotlin", _SOURCE)})
    sites = [
        (c.line, c.target_name, c.receiver_name, c.argument_count)
        for c in parsed["a/Bits.kt"].calls
        if c.line >= 8
    ]
    assert sites == [
        (8, "and", "byte", 1),
        (9, "and", "byte", 1),
        (10, "and", "x", 1),
        (11, "shr", "byte", 1),
    ]


def test_infix_calls_bind_the_receiver_typed_overload(tmp_path: Path) -> None:
    parsed = _parse_all(tmp_path, {"a/Bits.kt": ("kotlin", _SOURCE)})
    edges = sorted((callee, origin) for _, callee, _, origin in _edges(parsed, tmp_path))
    # ``x and 3`` is Int's own ``and``, outside the repository.
    assert edges == [
        ("a/Bits.kt::and#1(Byte,Int)", "receiver_extension_same_file"),
        ("a/Bits.kt::and#1(Byte,Long)", "receiver_extension_same_file"),
        ("a/Bits.kt::shr", "receiver_extension_same_file"),
    ]


def test_a_builtin_bitwise_member_beats_a_same_named_extension(tmp_path: Path) -> None:
    """``i and 0x3f`` is ``Int.and(Int)``; only a ``Long`` argument reaches ``Int.and(Long)``."""
    source = """\
package a

internal infix fun Int.and(other: Long): Long = toLong() and other

fun f(i: Int) {
    val a = i and 0x3f
    val b = i.and(0x3f)
    val c = i and 0xffL
}
"""
    parsed = _parse_all(tmp_path, {"a/Bits.kt": ("kotlin", source)})
    resolver = CallResolver(parsed, {p: set() for p in parsed}, repo_path=str(tmp_path))
    calls = [c for c in parsed["a/Bits.kt"].calls if c.line >= 6]
    found = [(rc.line, rc.callee_id) for rc in resolver.resolve_file("a/Bits.kt", calls)]
    assert found == [(8, "a/Bits.kt::and")]


def test_an_int_literal_fits_a_long_member(tmp_path: Path) -> None:
    """``n and 0xff`` on a ``Long`` is ``Long.and(Long)``, not a repo ``Long.and(Int)``."""
    source = """package a

internal infix fun Long.and(other: Int): Long = this

fun f(n: Long) {
    val a = n and 0xff
}
"""
    parsed = _parse_all(tmp_path, {"a/Bits.kt": ("kotlin", source)})
    resolver = CallResolver(parsed, {p: set() for p in parsed}, repo_path=str(tmp_path))
    calls = [c for c in parsed["a/Bits.kt"].calls if c.line >= 6]
    assert resolver.resolve_file("a/Bits.kt", calls) == []
