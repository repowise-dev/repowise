"""The type-parameter names a Java or Kotlin declaration header declares."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.call_receiver_typing.java import _header_type_parameters


@pytest.mark.parametrize(
    ("header", "names"),
    [
        ("public class Box<T> {", {"T"}),
        ("class Pair<K extends Comparable<K>, V> {", {"K", "V"}),
        ("public static <T> T first(List<T> items) {", {"T"}),
        ("@Override public <R> R map(Function<T, R> fn) {", {"R"}),
        ("public void run() {", set()),
        ("fun <T> f(t: T) = t", {"T"}),
        ("inline fun <reified T> f(): T", {"T"}),
        ("suspend fun <T : Comparable<T>> max(a: T, b: T): T", {"T"}),
        ("class Box<out T>", {"T"}),
        ("interface Sink<in T, out R>", {"T", "R"}),
        ("class Holder<@UnsafeVariance T>", {"T"}),
        ("fun f(leaf: Leaf) = leaf.m()", set()),
    ],
)
def test_header_type_parameters(header: str, names: set[str]) -> None:
    assert _header_type_parameters(header) == names
