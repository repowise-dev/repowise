"""The ``>`` of ``->`` and of ``=>`` is an arrow: it closes no ``<`` of a type's arguments."""

from __future__ import annotations

from repowise.core.ingestion.return_types import signature_parameters
from repowise.core.ingestion.type_names import bare_type_name, strip_type_arguments


def test_a_function_type_in_type_arguments_does_not_end_them_early_in_a_parameter_list() -> None:
    assert signature_parameters("f(a: Map<(Int) -> K, V>, b: Int) -> Unit") == [
        "a: Map<(Int) -> K, V>",
        " b: Int",
    ]
    assert signature_parameters("f(a: Map<(x) => K, V>, b)") == ["a: Map<(x) => K, V>", " b"]
    # an arrow in no type arguments still parts nothing, and a closed group still closes
    assert signature_parameters("f(cb: (Int) -> String, m: Map<K, V>)") == [
        "cb: (Int) -> String",
        " m: Map<K, V>",
    ]


def test_type_arguments_that_hold_a_function_type_are_removed_whole() -> None:
    assert strip_type_arguments("Map<(A) -> B, C>") == "Map"
    assert strip_type_arguments("Map<(A) => B, C>.Entry") == "Map.Entry"
    assert bare_type_name("Map<(A) -> B, C>") == "Map"
    # the arrow of a function type outside type arguments stays
    assert strip_type_arguments("(A) -> B") == "(A) -> B"
    assert strip_type_arguments("List<(A) -> B> -> C") == "List -> C"
