"""A Kotlin call with one type argument and one argument, ``f<T>(x)``, is a call site."""

from __future__ import annotations

from pathlib import Path

from tests.unit.ingestion.test_call_resolver_strategies import _parse_all

_SOURCE = """\
package a

class Holder
class Pair2<A, B>(val a: A, val b: B)

fun build(x: Int): Int = x
fun <T> pick(value: Int): Int = value

fun f(args: Array<String>, holder: Holder, n: Int) {
    start<Holder>(*args)
    val a = build<Int>(n)
    val b = pick<Int>(1)
    val c = holder.get<Int>(n)
    val d = this.get<Int>(n)
    val e = holder.items.get<Int>(n)
    val g = pick<Int>()
    val h = Pair2<Int, Int>(1, 2)
    val i = this.items.get<Int>(n)
    val j = holder!!.get<Int>(n)
    val k = "s".get<Int>(n)
    val l = pick<a.B>(n)
    val m = !pick<Int>(n)
    val bracketed = (n < n) > (n)
    val plain = "s".get(n)
}
"""


def test_a_call_with_one_type_argument_and_one_argument_is_a_call_site(tmp_path: Path) -> None:
    parsed = _parse_all(tmp_path, {"a/F.kt": ("kotlin", _SOURCE)})
    sites = [
        (c.line, c.target_name, c.receiver_name, c.argument_count)
        for c in parsed["a/F.kt"].calls
        if c.line >= 9
    ]
    assert (10, "start", None, 1) in sites
    assert (11, "build", None, 1) in sites
    assert (12, "pick", None, 1) in sites
    assert (13, "get", "holder", 1) in sites
    assert (14, "get", "this", 1) in sites
    assert (15, "get", "holder.items", 1) in sites
    # Two type arguments or no argument are read by the grammar as a call already.
    assert (16, "pick", None, 0) in sites
    assert (17, "Pair2", None, 2) in sites
    # The receivers a plain member call takes, a qualified type argument and a prefix operator.
    assert (18, "get", "items", 1) in sites
    assert (19, "get", "holder", 1) in sites
    assert (20, "get", '""', 1) in sites
    assert (21, "pick", None, 1) in sites
    assert (22, "pick", None, 1) in sites
    # The same receiver without a type argument.
    assert (24, "get", '""', 1) in sites
    # A comparison whose left side is bracketed is no call.
    assert not [site for site in sites if site[0] == 23]
