"""Walker facts behind ``primitive_obsession``: constructors, signatures fixed
by another declaration, and how many parameters are typed scalars or strings."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

FIVE = "a: int, b: int, c: int, d: int, e: int"


def _fns(path: str, language: str, source: str) -> dict[str, object]:
    return {f.name: f for f in walk_file(path, language, source.encode()).functions}


def test_java_constructor_override_and_scalar_count():
    fns = _fns(
        "Foo.java",
        "java",
        """class Foo {
            Foo(int a, String b, long c, Bar d, Baz e, int f, int g) {}
            @Override public void run(int a, int b, int c, int d, int e) {}
            public void pub(int a, int b, int c, int d, int e) {}
            @SuppressWarnings("override") void quiet(int a, int b, int c, int d, int e) {}
            void override(int a, int b, int c, int d, int e) {}
            void mix(int[] a, final String b, java.lang.String c, List<String> d, Map<A, B> e, int... f) {}
        }""",
    )
    assert fns["Foo"].is_constructor and not fns["Foo"].signature_fixed
    assert (fns["Foo"].typed_param_count, fns["Foo"].primitive_param_count) == (7, 5)
    assert fns["run"].signature_fixed
    # Java ``public`` is not read as published API.
    assert not fns["pub"].signature_fixed
    # A string argument or a name that says "override" marks nothing.
    assert not fns["quiet"].signature_fixed
    assert not fns["override"].signature_fixed
    # A generic container is not a scalar; an array or varargs of one is.
    assert fns["mix"].primitive_param_count == 4 and not fns["mix"].is_constructor


@pytest.mark.parametrize(
    ("member", "fixed"),
    [
        ("public override void M(int a, int b, int c, int d, int e) {}", True),
        ('[DllImport("x")] static extern int M(int a, int b, int c, int d, int e);', True),
        ("void IBar.M(int a, int b, int c, int d, int e) {}", True),
        ("[DataRow(1, 2, 3, 4, 5)] public void M(int a, int b, int c, int d, int e) {}", True),
        ('[LoggerMessage(1, "{a}")] static partial void M(int a, int b, int c, int d, int e);', True),
        ("public void M(int a, int b, int c, int d, int e) {}", True),
        ("protected void M(int a, int b, int c, int d, int e) {}", True),
        ('[Obsolete("override")] void M(int a, int b, int c, int d, int e) {}', False),
        ("internal void M(int a, int b, int c, int d, int e) {}", False),
        ("private void M(int a, int b, int c, int d, int e) {}", False),
        ("private protected void M(int a, int b, int c, int d, int e) {}", False),
    ],
)
def test_csharp_fixed_signatures(member: str, fixed: bool):
    fns = _fns("A.cs", "csharp", f"public class A {{ {member} }}")
    assert fns["M"].signature_fixed is fixed


def test_csharp_public_member_of_internal_type_is_not_api():
    fns = _fns(
        "A.cs",
        "csharp",
        "internal class A { public void M(int a, int b, int c, int d, int e) {} }",
    )
    assert not fns["M"].signature_fixed


def test_csharp_scalar_count_strips_modifiers_and_nullability():
    fns = _fns(
        "A.cs",
        "csharp",
        "class A { A(int a, string b, long c, int d, int e, int f, int g) {}"
        " void M(int a, int b, int? c, ref int d, out string e, Task t) {} }",
    )
    assert fns["A"].is_constructor
    assert (fns["M"].typed_param_count, fns["M"].primitive_param_count) == (6, 5)


def test_python_override_decorator_and_name():
    fns = _fns(
        "a.py",
        "python",
        "class C:\n"
        "    @override\n"
        f"    def run(self, {FIVE}):\n        pass\n"
        "    @typing.override\n"
        f"    def walk(self, {FIVE}):\n        pass\n"
        f"    def override(self, {FIVE}):\n        pass\n",
    )
    assert fns["run"].signature_fixed and fns["walk"].signature_fixed
    assert not fns["override"].signature_fixed


def test_typescript_override_modifier_and_name():
    five = "a: number, b: number, c: number, d: number, e: number"
    fns = _fns(
        "a.ts",
        "typescript",
        f"class A extends B {{ override run({five}) {{}} public pub({five}) {{}} }}\n"
        f"function override({five}) {{}}\n",
    )
    assert fns["run"].signature_fixed
    assert not fns["pub"].signature_fixed
    assert not fns["override"].signature_fixed


def test_kotlin_override_modifier():
    source = "class A : B() {\n    override fun run(a: Int) {}\n    fun own(a: Int) {}\n}\n"
    fns = _fns("A.kt", "kotlin", source)
    assert fns["run"].signature_fixed and not fns["own"].signature_fixed


def test_rust_trait_impl_is_fixed_and_inherent_impl_is_not():
    fns = _fns(
        "a.rs",
        "rust",
        "impl Tr for X { fn run(&self, a: i32, b: &str, c: u64, d: String, e: &mut Vec<u8>) {} }\n"
        "impl X { fn own(&self, a: i32, b: &str, c: u64, d: String, e: Vec<u8>) {} }",
    )
    assert fns["run"].signature_fixed
    assert not fns["own"].signature_fixed
    assert (fns["own"].typed_param_count, fns["own"].primitive_param_count) == (5, 4)


def test_untyped_parameters_are_in_neither_count():
    fns = _fns("a.py", "python", "def f(a, b, c, d, e: int):\n    pass\n")
    assert (fns["f"].typed_param_count, fns["f"].primitive_param_count) == (1, 1)
    fns = _fns("b.py", "python", "def g(a, b, c, d, e):\n    pass\n")
    assert fns["g"].typed_param_count == 0
    typed = _fns("c.py", "python", "class C:\n    def __init__(self, a: int, b: str):\n        pass\n")
    assert typed["__init__"].is_constructor
    assert typed["__init__"].primitive_param_count == 2


def test_optional_scalars_count_as_scalars():
    fns = _fns(
        "a.py",
        "python",
        "def f(a: str, b: str | None, c: Optional[int], d: Path, e: int):\n    pass\n",
    )
    assert fns["f"].primitive_param_count == 4


def test_dart_num_is_a_scalar():
    fns = _fns("a.dart", "dart", "void f(num a, int b, String c, Foo d) {}\n")
    assert (fns["f"].typed_param_count, fns["f"].primitive_param_count) == (4, 3)
