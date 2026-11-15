"""C# ``foreach`` loop variables typed from the collection they walk."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.languages.receiver_types import range_element_types, scan_ranges
from tests.unit.ingestion.test_chained_receiver_calls import _edges

LIB = (
    "csharp",
    "namespace A;\npublic class Item { public void Use() {} public object Clone() { return null; } }\n"
    "public class Comp<T, V> { public void Apply() {} }\n",
)
USE = "Lib.cs::Item::Use"
CLONE = "Lib.cs::Item::Clone"


def _uses(tmp_path: Path, members: str, body: str) -> set[str]:
    """The callees a ``Run(Item[] arr, IEnumerable<Item> seq)`` with *body* reaches."""
    source = (
        "namespace A;\nusing System.Collections.Generic;\npublic class C<T, V> {\n"
        f"{members}"
        "    public void Run(Item[] arr, IEnumerable<Item> seq) {\n"
        f"{body}"
        "    }\n    object Make() { return null; }\n}\n"
    )
    edges = _edges(tmp_path, {"Lib.cs": LIB, "C.cs": ("csharp", source)})
    return {callee for _, callee, _, _ in edges}


@pytest.mark.parametrize(
    ("members", "loop"),
    [
        ("    private readonly List<Item> _items = new();\n", "foreach (var x in _items)"),
        ("    public IReadOnlyCollection<Item> Items { get; }\n", "foreach (var x in Items)"),
        ("    public IList<Item> Items { get; }\n", "foreach (var x in this.Items)"),
        ("", "foreach (var x in arr)"),
        ("", "foreach (var x in seq)"),
        ("", "await foreach (var x in seq)"),
        ("", "foreach (Item x in seq)"),
    ],
)
def test_the_loop_variable_is_the_element(tmp_path: Path, members: str, loop: str) -> None:
    assert USE in _uses(tmp_path, members, f"        {loop} {{ x.Use(); }}\n")


def test_a_generic_element_keeps_its_arity(tmp_path: Path) -> None:
    members = "    private List<Comp<T, V>> _comps = new();\n"
    body = "        foreach (var x in _comps) { x.Apply(); }\n"
    assert "Lib.cs::Comp::Apply" in _uses(tmp_path, members, body)


@pytest.mark.parametrize(
    ("members", "body"),
    [
        # An untyped local of the head's name hides the field.
        (
            "    private List<Item> _items = new();\n",
            "        var _items = Make();\n        foreach (var x in _items) { x.Use(); }\n",
        ),
        # A typed local or a pattern variable of the head's name hides the field.
        (
            "    private List<Item> _items = new();\n",
            "        Dictionary<string, Item> _items = new();\n"
            "        foreach (var x in _items) { x.Use(); }\n",
        ),
        (
            "    private List<Item> r = new();\n",
            "        if (Make() is Item r) { foreach (var x in r) { x.Use(); } }\n",
        ),
        # A dictionary walks pairs, not values.
        (
            "    private Dictionary<string, Item> _map = new();\n",
            "        foreach (var x in _map) { x.Use(); }\n",
        ),
    ],
)
def test_untypable_loops_stay_untyped(tmp_path: Path, members: str, body: str) -> None:
    assert USE not in _uses(tmp_path, members, body)


def test_scan_and_shapes() -> None:
    scan = scan_ranges("foreach (var it in this.Items) { }\nforeach (var y in Rules) { }\n", "csharp")
    assert [(c.value, c.head, c.member) for c in scan.clauses] == [
        ("it", "this", "Items"),
        ("y", "Rules", ""),
    ]
    assert range_element_types("List<Comp<T, V>>", "csharp") == (None, "Comp`2")
    assert range_element_types("Item[]", "csharp") == (None, "Item")
    assert range_element_types("Item[,]", "csharp") == (None, "Item")
    assert range_element_types("Item[][]", "csharp") is None
    assert range_element_types("Dictionary<string, Item>", "csharp") is None


@pytest.mark.parametrize(
    ("decl", "use"),
    [
        ("Item[] a = Make();", "a.Clone();"),
        ("Item[][] b = Make();", "b.Clone();"),
        ("Item[,] c = Make();", "c.Clone();"),
    ],
)
def test_an_array_is_never_typed_as_its_element(tmp_path: Path, decl: str, use: str) -> None:
    assert CLONE not in _uses(tmp_path, "", f"        {decl}\n        {use}\n")


def test_foreach_over_a_multidimensional_array_types_the_element(tmp_path: Path) -> None:
    body = "        Item[,] grid = Make();\n        foreach (var x in grid) { x.Use(); }\n"
    assert USE in _uses(tmp_path, "", body)


def test_foreach_over_a_jagged_array_stays_untyped(tmp_path: Path) -> None:
    body = "        Item[][] jag = Make();\n        foreach (var x in jag) { x.Use(); }\n"
    assert USE not in _uses(tmp_path, "", body)
