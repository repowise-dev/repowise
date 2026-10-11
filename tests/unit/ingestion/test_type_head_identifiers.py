"""Characterisation: what the six type-head extractors in ``parser_helpers.py``
actually produce for a table of type spellings, per language.

``TYPE_HEAD_EXTRACTORS`` dispatches ``@param.type`` captures (constructor /
method parameters, fields, generics, heritage, ...) to a language-specific
extractor: ``_head_type_identifier`` (C#, the dict default), ``_go_...``,
``_c_...`` (shared by C and C++), ``_ts_...`` (shared by TypeScript and
JavaScript), ``_java_...``, ``_kotlin_...``. Each answers "what is the head
type name" its own way, with its own builtin-filter list and its own
generic-argument recursion (or lack of it).

Driven through the real parser (never hand-built tree-sitter nodes) so the
pinned values are what production actually emits, not what the extractor's
docstring claims. Committed against pre-existing behaviour: rows marked
"defect" are bugs, pinned anyway, because this file's job is to make the next
change to it show up as a legible diff rather than a silent regression.

Rust and Pascal are excluded: Rust uses the default C# extractor (already
covered by the C# rows) via no per-language entry, and Pascal's grammar
(tree_sitter_pascal) is unavailable in this environment.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

# (language, extension, case id, source, expected (type_name, origin) pairs).
# Each source declares exactly one type-bearing construct so a failure names
# one case. Grouped per language in the order TYPE_HEAD_EXTRACTORS lists them
# (csharp is the dict's default fallback, listed first).
CASES: list[tuple[str, str, str, str, list[tuple[str, str]]]] = [
    # =========================================================================
    # C# — _head_type_identifier (the TYPE_HEAD_EXTRACTORS default)
    # =========================================================================
    ("csharp", "cs", "plain", "class Probe {\n    void M(Basket a) {}\n}\n", [("Basket", "method_param")]),
    (
        "csharp",
        "cs",
        "generic",
        "class Probe {\n    void M(IList<Basket> a) {}\n}\n",
        # Generic args are captured separately via type_argument_list, so
        # both the head and the arg show up as their own refs.
        [("IList", "method_param"), ("Basket", "generic_argument")],
    ),
    (
        "csharp",
        "cs",
        "qualified",
        "class Probe {\n    void M(Acme.Catalog.IRepository a) {}\n}\n",
        [("IRepository", "method_param")],
    ),
    (
        "csharp",
        "cs",
        "ref-readonly-generic-wrapper",
        "class Probe {\n    void M(ref readonly Span<byte> a) {}\n}\n",
        [("Span", "method_param")],
    ),
    ("csharp", "cs", "nullable-wrapper", "class Probe {\n    void M(Foo? a) {}\n}\n", [("Foo", "method_param")]),
    ("csharp", "cs", "array-wrapper", "class Probe {\n    void M(Foo[] a) {}\n}\n", [("Foo", "method_param")]),
    ("csharp", "cs", "builtin-string", "class Probe {\n    void M(string a) {}\n}\n", []),
    ("csharp", "cs", "builtin-int", "class Probe {\n    void M(int a) {}\n}\n", []),
    ("csharp", "cs", "single-upper-generic-param", "class Probe {\n    void M(T a) {}\n}\n", []),
    # =========================================================================
    # Go — _go_head_type_identifier
    # =========================================================================
    ("go", "go", "plain", "package main\n\nfunc F(a Options) {}\n", [("Options", "param_type")]),
    (
        "go",
        "go",
        "generic",
        # Defect: unlike C#/Java/C++, Go's query has no separate capture for
        # a generic type argument — ``Inner`` is silently dropped, not just
        # filtered.
        "package main\n\nfunc F(a List[Inner]) {}\n",
        [("List", "param_type")],
    ),
    (
        "go",
        "go",
        "qualified",
        "package main\n\nfunc F(a dynacache.Cache) {}\n",
        [("Cache", "param_type")],
    ),
    ("go", "go", "pointer-wrapper", "package main\n\nfunc F(a *Cache) {}\n", [("Cache", "param_type")]),
    ("go", "go", "slice-wrapper", "package main\n\nfunc F(a []Partition) {}\n", [("Partition", "param_type")]),
    (
        "go",
        "go",
        "map-value-wrapper",
        "package main\n\nfunc F(a map[string]Config) {}\n",
        [("Config", "param_type")],
    ),
    ("go", "go", "builtin-string", "package main\n\nfunc F(a string) {}\n", []),
    ("go", "go", "builtin-error", "package main\n\nfunc F(a error) {}\n", []),
    ("go", "go", "single-upper-generic-param", "package main\n\nfunc F(a T) {}\n", []),
    # =========================================================================
    # C — _c_head_type_identifier (shared with C++)
    # =========================================================================
    ("c", "c", "plain", "void f(JSON_Value *v) {}\n", [("JSON_Value", "param_type")]),
    ("c", "c", "named-struct-ref", "void f(struct JSON_Object *o) {}\n", [("JSON_Object", "param_type")]),
    ("c", "c", "builtin-int", "void f(int x) {}\n", []),
    ("c", "c", "builtin-unsigned-long", "void f(unsigned long x) {}\n", []),
    ("c", "c", "stdlib-typedef-size_t", "void f(size_t x) {}\n", []),
    # C has no ``::`` qualifier or template syntax to exercise those rows.
    # =========================================================================
    # C++ — _c_head_type_identifier
    # =========================================================================
    ("cpp", "cpp", "plain", "void f(Widget *w) {}\n", [("Widget", "param_type")]),
    (
        "cpp",
        "cpp",
        "generic",
        # Unlike Go, C++'s template_argument_list pattern DOES capture the
        # inner arg as its own ref.
        "void f(std::vector<Widget> v) {}\n",
        [("vector", "param_type"), ("Widget", "param_type")],
    ),
    ("cpp", "cpp", "qualified", "void f(NS::Widget *w) {}\n", [("Widget", "param_type")]),
    ("cpp", "cpp", "builtin-int", "void f(int x) {}\n", []),
    ("cpp", "cpp", "stdlib-typedef-size_t", "void f(size_t x) {}\n", []),
    ("cpp", "cpp", "single-upper-generic-param", "void f(T x) {}\n", []),
    # =========================================================================
    # TypeScript — _ts_head_type_identifier (shared with JavaScript)
    # =========================================================================
    ("typescript", "ts", "plain", "function f(a: Foo) {}\n", [("Foo", "param_type")]),
    (
        "typescript",
        "ts",
        "generic-builtin-outer",
        # Defect: the builtin outer (Promise) is correctly filtered, but the
        # inner user type (Foo) is never captured separately — TS has no
        # equivalent of C#'s type_argument_list rescue. Produces nothing.
        "function f(a: Promise<Foo>) {}\n",
        [],
    ),
    (
        "typescript",
        "ts",
        "generic-user-outer",
        # Defect: same loss when the outer is a non-builtin — only the
        # outer head (Container) survives; the inner arg (Foo) is dropped.
        "function f(a: Container<Foo>) {}\n",
        [("Container", "param_type")],
    ),
    ("typescript", "ts", "qualified", "function f(a: ns.Foo) {}\n", [("Foo", "param_type")]),
    ("typescript", "ts", "array-wrapper", "function f(a: Foo[]) {}\n", [("Foo", "param_type")]),
    ("typescript", "ts", "builtin-string", "function f(a: string) {}\n", []),
    ("typescript", "ts", "builtin-bare-Promise", "function f(a: Promise) {}\n", []),
    ("typescript", "ts", "single-upper-generic-param", "function f(a: T) {}\n", []),
    (
        "typescript",
        "ts",
        "union-type",
        "function f(a: Foo | null) {}\n",
        [("Foo", "param_type")],
    ),
    # =========================================================================
    # JavaScript — _ts_head_type_identifier (no type syntax to feed it)
    # =========================================================================
    ("javascript", "js", "no-type-annotations", "function f(a) {}\n", []),
    # =========================================================================
    # Java — _java_head_type_identifier
    # =========================================================================
    ("java", "java", "plain", "class Probe {\n    void m(Bar a) {}\n}\n", [("Bar", "param_type")]),
    (
        "java",
        "java",
        "generic",
        "class Probe {\n    void m(List<Foo> a) {}\n}\n",
        [("Foo", "param_type")],
    ),
    (
        "java",
        "java",
        "qualified",
        "class Probe {\n    void m(com.x.y.Zeta a) {}\n}\n",
        [("Zeta", "param_type")],
    ),
    (
        "java",
        "java",
        "qualified-single-upper",
        # Defect: the qualifier-stripped rightmost segment is a real class
        # name (Z), but the single-uppercase-letter heuristic (meant for
        # generic params like T/K/V) fires on it anyway and drops it.
        "class Probe {\n    void m(com.x.y.Z a) {}\n}\n",
        [],
    ),
    ("java", "java", "array-wrapper", "class Probe {\n    void m(Foo[] a) {}\n}\n", [("Foo", "param_type")]),
    ("java", "java", "builtin-int", "class Probe {\n    void m(int a) {}\n}\n", []),
    ("java", "java", "builtin-String", "class Probe {\n    void m(String a) {}\n}\n", []),
    ("java", "java", "single-upper-generic-param", "class Probe {\n    void m(T a) {}\n}\n", []),
    # =========================================================================
    # Kotlin — _kotlin_head_type_identifier
    # =========================================================================
    ("kotlin", "kt", "plain-ctor-param", "class Probe(val a: Bar)\n", [("Bar", "ctor_param")]),
    (
        "kotlin",
        "kt",
        "generic-ctor-param",
        "class Probe(val a: List<Foo>)\n",
        [("Foo", "ctor_param")],
    ),
    (
        "kotlin",
        "kt",
        "qualified-ctor-param",
        "class Probe(val a: com.x.Zeta)\n",
        [("Zeta", "ctor_param")],
    ),
    (
        "kotlin",
        "kt",
        "qualified-single-upper-ctor-param",
        # Defect: same single-uppercase-letter false positive as Java, on
        # the rightmost segment of a qualified name.
        "class Probe(val a: com.x.Z)\n",
        [],
    ),
    ("kotlin", "kt", "nullable-wrapper", "class Probe(val a: Foo?)\n", [("Foo", "ctor_param")]),
    ("kotlin", "kt", "builtin-Int", "class Probe(val a: Int)\n", []),
    ("kotlin", "kt", "builtin-Unit-return", "fun f(): Unit {}\n", []),
    ("kotlin", "kt", "single-upper-generic-param", "class Probe(val a: T)\n", []),
    (
        "kotlin",
        "kt",
        "function-param-mislabeled-return-type",
        # Not a value bug, but a visibly wrong origin tag: ``parameter`` is
        # deliberately absent from _PARAM_ORIGIN_BY_ANCESTOR (it would
        # collide with C#'s ctor-param walk), so a function parameter's
        # origin falls through to the enclosing function_declaration and
        # is tagged "return_type" even though it is a parameter.
        "class Probe {\n    fun m(a: Bar) {}\n}\n",
        [("Bar", "return_type")],
    ),
    (
        "kotlin",
        "kt",
        "heritage-plain-interface",
        "interface Probe : Bar\n",
        [("Bar", "extends")],
    ),
    (
        "kotlin",
        "kt",
        "heritage-with-constructor-call",
        # Defect: ``delegation_specifier (user_type)`` only matches a bare
        # supertype name. ``Qux()`` wraps the user_type one level deeper in
        # a constructor_invocation, so the pattern never fires — a
        # superclass named with call syntax produces no type ref at all
        # (heritage.extends still records it separately; this is only the
        # file-level param.type edge).
        "class Probe : Qux() {}\n",
        [],
    ),
]


def _parse(language: str, ext: str, source: str):
    info = FileInfo(
        path=f"probe.{ext}",
        abs_path=f"/tmp/probe.{ext}",
        language=language,
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode())


@pytest.mark.parametrize(
    ("language", "ext", "case_id", "source", "expected"),
    CASES,
    ids=[f"{c[0]}-{c[2]}" for c in CASES],
)
def test_type_head_identifier_for_spelling(
    language: str,
    ext: str,
    case_id: str,
    source: str,
    expected: list[tuple[str, str]],
) -> None:
    parsed = _parse(language, ext, source)
    got = [(t.type_name, t.origin) for t in parsed.type_refs]
    assert sorted(got) == sorted(expected)
