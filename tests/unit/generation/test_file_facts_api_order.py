"""The file page's API list leads with what other code calls, and file
neighbours split production from tests."""

from __future__ import annotations

from repowise.core.generation.page_generator.file_facts import file_facts, symbol_entries


def _sym(name: str, *, kind: str = "function", parent: str = "", callers: int = 0, doc: str = ""):
    return {
        "name": name,
        "kind": kind,
        "visibility": "public",
        "signature": f"def {name}()" if kind != "class" else f"class {name}",
        "docstring": doc,
        "parent_name": parent or None,
        "callers": callers,
    }


def _declared(entries) -> list[str]:
    return [e["declared"] for e in entries]


def test_a_type_ranks_by_its_own_and_its_members_callers():
    entries = symbol_entries(
        [
            _sym("busy_function", callers=5),
            _sym("Store", kind="class", callers=1),
            _sym("put", kind="method", parent="Store", callers=3),
            _sym("get", kind="method", parent="Store", callers=4),
            _sym("idle"),
        ]
    )
    # Store: 1 + 3 + 4 = 8 outranks busy_function's 5; idle keeps the tail.
    assert _declared(entries) == ["`class Store`", "`def busy_function()`", "`def idle()`"]
    assert [m["declared"] for m in entries[0]["members"]] == ["`def get()`", "`def put()`"]


def test_ties_keep_declaration_order():
    entries = symbol_entries([_sym("b"), _sym("a"), _sym("c")])
    assert _declared(entries) == ["`def b()`", "`def a()`", "`def c()`"]


def test_a_method_seen_before_its_class_is_filled_in_by_the_class():
    entries = symbol_entries(
        [
            _sym("put", kind="method", parent="Store", callers=2),
            _sym("Store", kind="class", callers=7, doc="Keeps things. More detail."),
        ]
    )
    assert len(entries) == 1
    store = entries[0]
    assert store["declared"] == "`class Store`"
    assert store["doc"] == "Keeps things."
    assert store["callers"] == 7
    assert [m["declared"] for m in store["members"]] == ["`def put()`"]


def _ctx():
    from types import SimpleNamespace

    return SimpleNamespace(
        symbols=[],
        docstring=None,
        kg_node_summary="",
        is_test=False,
        kg_tags=[],
        file_path="lib/store.py",
        dependencies=[],
        dependents=["app/main.py", "tests/test_store.py", "tests/unit/test_more.py"],
    )


def test_importers_split_production_paths_from_a_test_count():
    importers = file_facts(_ctx())["importers"]
    assert importers["code_paths"] == ["app/main.py"]
    assert importers["tests"] == 2
    assert importers["count"] == 3
