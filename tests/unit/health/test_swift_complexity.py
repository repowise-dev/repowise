"""The Swift complexity map, one decision at a time (#3437).

Swift had no ``LanguageNodeMap``: ``walk_file`` returned no functions for a
Swift file and health could not score it. Each test here pins one counting
choice of the map. Where Kotlin has the same construct, the Kotlin twin is
walked beside it, so a difference between the two languages is a choice made
here and not an accident.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.languages import get_language_map, has_health_dialect


def _walk(source: str, language: str = "swift"):
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    extension = {"swift": "swift", "kotlin": "kt"}[language]
    return walk_file(f"a.{extension}", language, source.encode())


def _shape(source: str, language: str = "swift") -> dict[str, tuple[int, int]]:
    """``name -> (ccn, max_nesting)``."""
    return {f.name: (f.ccn, f.max_nesting) for f in _walk(source, language).functions}


def _f(body: str, language: str = "swift") -> tuple[int, int]:
    """``(ccn, max_nesting)`` of a function ``f`` with *body*."""
    source = {
        "swift": f"func f(a: Int, b: Int, xs: [Int]) -> Int {{\n{body}\n    return 0\n}}\n",
        "kotlin": f"fun f(a: Int, b: Int, xs: List<Int>): Int {{\n{body}\n    return 0\n}}\n",
    }[language]
    return _shape(source, language)["f"]


def test_swift_has_a_map():
    assert has_health_dialect("swift")
    assert get_language_map("swift") is not None


def test_a_function_with_no_decisions():
    assert _f("    let c = a + b") == (1, 0)


# -- branches -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("swift", "kotlin", "expected"),
    [
        pytest.param("    if a > 0 { return 1 }", "    if (a > 0) { return 1 }", (2, 1), id="if"),
        pytest.param(
            "    if a > 0 { return 1 } else { return 2 }",
            "    if (a > 0) { return 1 } else { return 2 }",
            (2, 1),
            id="if-else",
        ),
        pytest.param(
            "    if a > 0 { return 1 } else if a < 0 { return 2 } else { return 3 }",
            "    if (a > 0) { return 1 } else if (a < 0) { return 2 } else { return 3 }",
            (3, 1),
            id="else-if-is-flat",
        ),
        pytest.param(
            "    if a > 0 { if b > 0 { return 1 } }",
            "    if (a > 0) { if (b > 0) { return 1 } }",
            (3, 2),
            id="nested-if",
        ),
        pytest.param(
            "    if a > 0 && b > 0 || a < -1 { return 1 }",
            "    if (a > 0 && b > 0 || a < -1) { return 1 }",
            (4, 1),
            id="and-or",
        ),
        pytest.param(
            "    for x in xs { if x > 0 { return x } }",
            "    for (x in xs) { if (x > 0) { return x } }",
            (3, 2),
            id="for",
        ),
        pytest.param(
            "    while a > 0 { if b > 0 { return 1 } }",
            "    while (a > 0) { if (b > 0) { return 1 } }",
            (3, 2),
            id="while",
        ),
        pytest.param(
            "    repeat { if b > 0 { return 1 } } while a > 0",
            "    do { if (b > 0) { return 1 } } while (a > 0)",
            (3, 2),
            id="repeat-while",
        ),
    ],
)
def test_swift_and_kotlin_agree(swift, kotlin, expected):
    assert _f(swift) == expected
    assert _f(kotlin, "kotlin") == expected


def test_guard_is_a_decision():
    """It has a condition and a path that leaves, like an ``if``."""
    assert _f("    guard a > 0 else { return 1 }") == (2, 1)
    assert _f("    guard a > 0, b > 0 else { return 1 }") == (2, 1)


def test_if_let_and_guard_let_are_the_same_decisions_with_a_binding():
    source = (
        "func f(a: Int?) -> Int {\n"
        "    guard let x = a else { return 0 }\n"
        "    if let y = a, y > x { return y }\n"
        "    return x\n"
        "}\n"
    )

    assert _shape(source) == {"f": (3, 1)}


def test_the_ternary_is_a_branch():
    assert _f("    let c = a > 0 ? 1 : 2") == (2, 1)
    # Each arm of a chain is a decision. How deep a chain nests is #3285's
    # question for every language outside TypeScript, so it is not pinned here.
    assert _f("    let c = a > 0 ? 1 : a < 0 ? -1 : 0")[0] == 3


def test_nil_coalescing_is_not_a_decision():
    """As Kotlin's ``?:`` is not."""
    source = "func f(a: Int?, b: Int?) -> Int {\n    let c = a ?? b ?? 0\n    return c\n}\n"
    kotlin = "fun f(a: Int?, b: Int?): Int {\n    val c = a ?: b ?: 0\n    return c\n}\n"

    assert _shape(source) == {"f": (1, 0)}
    assert _shape(kotlin, "kotlin") == {"f": (1, 0)}


# -- loops with a filter ----------------------------------------------------------


def test_a_for_where_filter_is_one_more_decision_and_no_deeper():
    plain = _f("    for x in xs { if x > 0 { return x } }")
    filtered = _f("    for x in xs where x > 1 { if x > 0 { return x } }")

    assert plain == (3, 2)
    assert filtered == (4, 2)
    # The filter is beside the body, not around it: on its own it is level 1.
    assert _f("    for x in xs where x > 1 { use(x) }") == (3, 1)


def test_a_generic_where_is_a_constraint_and_not_a_decision():
    source = (
        "func f<T>(x: T) -> Int where T: Equatable {\n    return 0\n}\n"
        "extension Array where Element == Int {\n    func g() -> Int { return 0 }\n}\n"
    )

    assert _shape(source) == {"f": (1, 0), "g": (1, 0)}


# -- switch -----------------------------------------------------------------------


def test_a_switch_with_simple_arms_is_one_decision():
    """The rule Kotlin's ``when`` follows: a flat dispatch counts once."""
    swift = (
        "func f(k: Int) -> Int {\n    switch k {\n    case 1: return 1\n    case 2, 3: return 2\n"
        "    default: return 0\n    }\n}\n"
    )
    kotlin = (
        "fun f(k: Int): Int {\n    when (k) {\n        1 -> return 1\n        2, 3 -> return 2\n"
        "        else -> return 0\n    }\n}\n"
    )

    assert _shape(swift) == {"f": (2, 1)}
    assert _shape(kotlin, "kotlin") == {"f": (2, 1)}


def test_a_switch_whose_arms_branch_counts_each_arm():
    swift = (
        "func f(k: Int, a: Int) -> Int {\n    switch k {\n"
        "    case 1:\n        if a > 0 { return 1 }\n        return 2\n"
        "    case 2:\n        return 3\n"
        "    default:\n        return 0\n    }\n}\n"
    )
    kotlin = (
        "fun f(k: Int, a: Int): Int {\n    when (k) {\n"
        "        1 -> {\n            if (a > 0) { return 1 }\n            return 2\n        }\n"
        "        2 -> return 3\n"
        "        else -> return 0\n    }\n}\n"
    )

    assert _shape(swift) == _shape(kotlin, "kotlin")
    assert _shape(swift)["f"][0] > 2


def test_a_case_where_guard_is_not_counted():
    """This grammar gives the guard no node of its own."""
    plain = (
        "func f(k: K) -> Int {\n    switch k {\n    case .b(let n): return n\n    default: return 0\n    }\n}\n"
    )
    guarded = plain.replace("case .b(let n):", "case .b(let n) where n > 2:")

    assert _shape(guarded) == _shape(plain) == {"f": (2, 1)}


# -- errors -----------------------------------------------------------------------


def test_do_catch_counts_each_catch_and_only_the_catch_nests():
    source = (
        "func f() -> Int {\n    do {\n        try g()\n    } catch let e as NSError {\n        return 1\n"
        "    } catch {\n        return 2\n    }\n    return 0\n}\n"
    )
    kotlin = (
        "fun f(): Int {\n    try {\n        g()\n    } catch (e: IllegalStateException) {\n        return 1\n"
        "    } catch (e: Exception) {\n        return 2\n    }\n    return 0\n}\n"
    )

    assert _shape(source) == {"f": (3, 1)}
    assert _shape(kotlin, "kotlin") == {"f": (3, 1)}


def test_a_catch_where_filter_is_one_more_decision():
    plain = "func f() -> Int {\n    do { try g() } catch E.x { return 1 }\n    return 0\n}\n"
    filtered = plain.replace("catch E.x {", "catch E.x where c {")

    assert _shape(plain)["f"][0] + 1 == _shape(filtered)["f"][0]


def test_try_marks_and_defer_are_not_decisions():
    source = (
        "func f() throws -> Int {\n    defer { cleanup() }\n    let a = try g()\n    let b = try? g()\n"
        "    let c = try! g()\n    return a\n}\n"
    )

    assert _shape(source) == {"f": (1, 0)}


def test_a_do_block_with_no_catch_adds_no_path_and_no_level():
    assert _f("    do { if a > 0 { return 1 } }") == (2, 1)


# -- what is a function -------------------------------------------------------------


def test_init_deinit_subscript_and_properties_are_units():
    source = (
        "class C {\n"
        "    var items: [Int] = []\n"
        "    init(x: Int, y: Int) { if x > 0 { items = [y] } }\n"
        "    deinit { if items.isEmpty { log() } }\n"
        "    subscript(i: Int) -> Int { if i > 0 { return 1 }; return items[i] }\n"
        "    var first: Int { get { if items.isEmpty { return 0 }; return items[0] } set { if newValue > 0 { items = [newValue] } } }\n"
        "    var count: Int { return items.isEmpty ? 0 : items.count }\n"
        "    var watched: Int = 0 { willSet { if newValue > 0 { log() } } didSet { log() } }\n"
        "    func plain(a: Int, b: String) -> Int { return a }\n"
        "}\n"
    )

    functions = {f.name: f for f in _walk(source).functions}

    assert {name: (f.ccn, f.param_count) for name, f in functions.items()} == {
        "init": (2, 2),
        "deinit": (2, 0),
        "subscript": (2, 1),
        # ``get`` and ``set`` are one unit, the property's.
        "first": (3, 0),
        "count": (2, 0),
        "watched": (2, 0),
        "plain": (1, 2),
    }
    assert functions["init"].is_constructor
    assert not functions["plain"].is_constructor
    assert (functions["subscript"].start_line, functions["first"].start_line) == (5, 6)


def test_a_stored_property_and_a_protocol_requirement_are_not_units():
    source = (
        "protocol Shape {\n    func area() -> Double\n    var name: String { get }\n}\n"
        "struct P {\n    var x: Int = 0\n    let y = 1\n}\n"
    )

    assert _walk(source).functions == []


def test_a_closure_counts_toward_the_function_that_holds_it():
    """Like a Kotlin lambda."""
    source = (
        "func f(xs: [Int]) -> [Int] {\n"
        "    let g = { (a: Int) -> Int in if a > 0 { return a }; return 0 }\n"
        "    return xs.map { $0 > 1 ? $0 : 0 }\n"
        "}\n"
    )
    kotlin = (
        "fun f(xs: List<Int>): List<Int> {\n"
        "    val g = { a: Int -> if (a > 0) a else 0 }\n"
        "    return xs.map { if (it > 1) it else 0 }\n"
        "}\n"
    )

    assert _shape(source) == {"f": (3, 1)}
    assert _shape(kotlin, "kotlin") == {"f": (3, 1)}


def test_a_nested_func_is_a_unit_of_its_own_and_a_closure_inside_it_is_its():
    """A function inside a function has its own row (#3281); the closure in
    ``inner`` counts toward ``inner``, not toward ``outer``."""
    source = (
        "func outer(xs: [Int]) -> Int {\n"
        "    if xs.isEmpty { return 0 }\n"
        "    func inner(q: Int) -> Int {\n"
        "        let pick = { (a: Int) -> Int in a > 0 ? a : 0 }\n"
        "        if q > 0 { return pick(q) }\n"
        "        return 0\n"
        "    }\n"
        "    return inner(q: xs[0])\n"
        "}\n"
    )

    functions = {f.name: (f.ccn, f.max_nesting, f.param_count) for f in _walk(source).functions}

    assert functions == {"outer": (2, 1, 1), "inner": (3, 1, 1)}


def test_a_local_type_s_members_are_units():
    source = (
        "func make() -> Int {\n"
        "    struct Local {\n"
        "        var n = 0\n"
        "        var doubled: Int { return n > 0 ? n * 2 : 0 }\n"
        "        func get() -> Int { if n > 0 { return n }; return 0 }\n"
        "    }\n"
        "    return Local().get()\n"
        "}\n"
    )

    walked = _walk(source)

    assert {f.name: f.ccn for f in walked.functions} == {"make": 1, "doubled": 2, "get": 2}
    assert [(c.name, c.method_count) for c in walked.classes] == [("Local", 2)]


def test_a_closure_at_file_level_is_a_unit_of_its_own():
    """Nothing holds it, so it gets a row, as a Kotlin file-level lambda does."""
    source = "let handler = { (a: Int) -> Int in\n    if a > 0 { return 1 }\n    return 0\n}\n"
    kotlin = "val handler = { a: Int ->\n    if (a > 0) 1 else 0\n}\n"

    assert _shape(source) == {"<anonymous@1>": (2, 1)}
    assert _shape(kotlin, "kotlin") == {"<anonymous@1>": (2, 1)}


def test_an_override_has_a_fixed_signature():
    source = "class C: B {\n    override func m(a: Int) -> Int { return a }\n    func n(a: Int) -> Int { return a }\n}\n"

    fixed = {f.name: f.signature_fixed for f in _walk(source).functions}

    assert fixed == {"m": True, "n": False}


# -- classes ------------------------------------------------------------------------


def test_struct_enum_extension_and_actor_are_classes_and_a_protocol_is_not():
    source = (
        "class A {\n    var n = 0\n    func a() -> Int { return self.n }\n}\n"
        "struct S {\n    var n = 0\n    func s() -> Int { return self.n }\n}\n"
        "enum E {\n    case one\n    func e() -> Int { return 1 }\n}\n"
        "extension S {\n    func x() -> Int { return self.n }\n}\n"
        "actor R {\n    var n = 0\n    func r() -> Int { return self.n }\n}\n"
        "protocol P {\n    func p() -> Int\n}\n"
    )

    classes = sorted((c.name, c.start_line, c.method_count) for c in _walk(source).classes)

    assert classes == [("A", 1, 1), ("E", 9, 1), ("R", 16, 1), ("S", 5, 1), ("S", 13, 1)]


def test_class_metrics_count_methods_and_stored_properties():
    source = (
        "class C {\n"
        "    var items: [Int] = []\n"
        "    let k = 1\n"
        "    init(x: Int) { self.items = [x] }\n"
        "    func a() -> Int { return self.items.count + self.k }\n"
        "    func b() -> Int { return self.k }\n"
        "}\n"
    )

    (cls,) = _walk(source).classes

    assert (cls.name, cls.method_count, cls.field_count, cls.lcom4) == ("C", 3, 2, 1)


def test_another_object_s_member_is_not_this_class_s_field():
    """``o.a`` names a member of ``o``. Read as the class's own ``a`` it would
    tie ``m2`` to ``m1``; the Kotlin twin keeps them apart the same way."""
    swift = (
        "class C {\n    var a = 0\n    var b = 0\n"
        "    func m1() -> Int { return self.a }\n"
        "    func m2(o: C) -> Int {\n        let x = o.a\n        return x + self.b\n    }\n}\n"
    )
    kotlin = (
        "class C {\n    var a = 0\n    var b = 0\n"
        "    fun m1(): Int { return this.a }\n"
        "    fun m2(o: C): Int {\n        val x = o.a\n        return x + this.b\n    }\n}\n"
    )

    (swift_class,) = _walk(swift).classes
    (kotlin_class,) = _walk(kotlin, "kotlin").classes

    assert swift_class.tcc == kotlin_class.tcc == 0.0


def test_self_dot_member_and_a_bare_member_both_reach_the_field():
    source = (
        "class C {\n    var a = 0\n"
        "    func explicit() -> Int { return self.a }\n"
        "    func implicit() -> Int { return a + 1 }\n}\n"
    )

    (cls,) = _walk(source).classes

    assert (cls.method_count, cls.field_count, cls.tcc) == (2, 1, 1.0)


def test_a_property_s_units_are_members_of_its_class():
    """Every function row inside the class is one of its methods: a computed
    property, a ``get`` / ``set`` pair, a ``willSet`` / ``didSet`` block and a
    subscript beside ``init`` and the ``func``."""
    source = (
        "class C {\n"
        "    var items: [Int] = []\n"
        "    var watched: Int = 0 { willSet { if newValue > 0 { log() } } didSet { log() } }\n"
        "    var total: Int { return items.count > 0 ? items[0] : 0 }\n"
        "    var first: Int { get { return items[0] } set { items = [newValue] } }\n"
        "    init(x: Int) { self.items = [x] }\n"
        "    subscript(i: Int) -> Int { return items[i] }\n"
        "    func a() -> Int { return total + watched }\n"
        "}\n"
    )

    walked = _walk(source)
    (cls,) = walked.classes

    rows = sorted(f.name for f in walked.functions)
    assert rows == ["a", "first", "init", "subscript", "total", "watched"]
    assert sorted(m.name for m in cls.methods) == rows
    assert cls.method_count == 6
    assert cls.max_method_ccn == 2


def test_a_computed_property_is_not_counted_as_a_field():
    """It stores nothing. ``doubled`` reads the one stored property and ``m``
    calls ``doubled``: one field, and the two members are one component."""
    source = (
        "struct S {\n"
        "    var n = 0\n"
        "    var doubled: Int { return n * 2 }\n"
        "    func m() -> Int { return doubled + 1 }\n"
        "}\n"
    )

    (cls,) = _walk(source).classes

    assert (cls.method_count, cls.field_count, cls.lcom4) == (2, 1, 1)

