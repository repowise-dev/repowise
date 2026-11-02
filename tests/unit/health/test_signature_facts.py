"""Walker facts behind ``primitive_obsession``: constructors, signatures fixed
by another declaration, and how many parameters are typed scalars or strings."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.ast_utils import _count_parameters, _parameter_list
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.complexity.signature import typed_param_counts

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
        ("void IBar.M(int a, int b, int c, int d, int e) {}", True),
        ("[DataRow(1, 2, 3, 4, 5)] public void M(int a, int b, int c, int d, int e) {}", True),
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


def test_csharp_bodyless_bindings_are_no_unit():
    # P/Invoke and source-generated partials declare a signature another
    # declaration fills in, so there is nothing here to score.
    fns = _fns(
        "A.cs",
        "csharp",
        'public class A { [DllImport("x")] static extern int M(int a, int b, int c, int d, int e);'
        ' [LoggerMessage(1, "{a}")] static partial void N(int a, int b, int c, int d, int e); }',
    )
    assert fns == {}


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


def test_java_framework_bound_parameters():
    fns = _fns(
        "C.java",
        "java",
        """class C {
            void route(@PathVariable String a, @PathVariable String b, @PathVariable("c") String c,
                @org.springframework.web.bind.annotation.PathVariable String d, String e) {}
            void query(@RequestParam(value = "x", required = false) String x, @RequestBody Form f) {}
            C(@MaxMindDbParameter(name = "ip") String ip, @JsonProperty("asn") long asn) {}
        }""",
    )
    route = fns["route"]
    assert (route.param_count, route.bound_param_count) == (5, 4)
    # Bound parameters leave the typed and scalar counts.
    assert (route.typed_param_count, route.primitive_param_count) == (1, 1)
    # An argument does not hide the binder; an object binder is not one.
    assert fns["query"].bound_param_count == 1
    assert fns["query"].typed_param_count == 1
    assert fns["C"].bound_param_count == 2


def test_kotlin_bound_parameter_annotations_sit_beside_the_parameter():
    from tree_sitter_language_pack import get_parser

    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.complexity.signature import bound_param_count

    src = b'fun f(@PathVariable a: String, @RequestParam("x") b: String, c: String) {}'
    root = get_parser("kotlin").parse(src).root_node
    params = next(c for c in root.children[0].children if c.type == "function_value_parameters")
    assert bound_param_count(params, get_language_map("kotlin")) == 2


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


def test_cpp_extern_c_linkage_fixes_the_signature():
    params = "(int a, const char* b, size_t c, int d, uint64_t* e)"
    fns = _fns(
        "a.cc",
        "cpp",
        f'extern "C" {{\nvoid api{params} {{ g(); }}\n'
        f'extern "C++" {{ void reentered{params} {{ g(); }} }}\n'
        f"struct S {{ void member{params} {{ g(); }} }};\n}}\n"
        f'extern "C" int single{params} {{ return 0; }}\n'
        f"void own{params} {{ g(); }}\n",
    )
    assert fns["api"].signature_fixed and fns["single"].signature_fixed
    assert not fns["own"].signature_fixed and not fns["reentered"].signature_fixed
    assert not fns["member"].signature_fixed
    assert fns["own"].primitive_param_count == 5


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


@pytest.mark.parametrize(
    ("language", "path", "source", "expected"),
    [
        (
            "java", "A.java",
            "class A {\n  A(String a, // String b,\n"
            "    String c, /* String d, */ String e) {}\n}\n",
            3,
        ),
        (
            "rust", "a.rs",
            "fn f(a: i32, // b: i32,\n     c: i32, /* d: i32, */ e: i32) {}\n",
            3,
        ),
        (
            "csharp", "A.cs",
            "class A {\n  void F(int a, // int b,\n"
            "    int c, /* int d, */ int e) {}\n}\n",
            3,
        ),
        (
            "c", "a.c",
            "int f(int a, int b, int c, int d, int e, int g, int h)\n{\n    return a;\n}\n",
            7,
        ),
        (
            "cpp", "a.cpp",
            "int f(int a, int b, int c, int d, int e, int g, int h)\n{\n    return a;\n}\n",
            7,
        ),
        (
            "kotlin", "A.kt",
            "fun f(a: Int, b: Int, c: Int, d: Int, e: Int, g: Int, h: Int): Int {\n"
            "    return a\n}\n",
            7,
        ),
    ],
    ids=["java-comments", "rust-comments", "csharp-comments", "c", "cpp", "kotlin"],
)
def test_reported_parameter_counts(language: str, path: str, source: str, expected: int):
    (fn,) = walk_file(path, language, source.encode()).functions
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == (expected,) * 3


@pytest.mark.parametrize(
    ("language", "source", "expected"),
    [
        ("c", "int f(void) { return 0; }", (0, 0, 0)),
        ("c", "int f(void /* no parameters */) { return 0; }", (0, 0, 0)),
        ("c", "int f(void *p) { return 0; }", (1, 1, 0)),
        ("cpp", "int A::f(int a, int b) { return a; }", (2, 2, 2)),
        ("c", "char *f(int a, int b) { return 0; }", (2, 2, 2)),
        ("cpp", "int &f(int a, int b) { return a; }", (2, 2, 2)),
        ("cpp", "int f(int a, ...) { return a; }", (2, 1, 1)),
        ("cpp", "int f(...) { return 0; }", (1, 0, 0)),
        (
            "c", "int f(void (*cb)(int a, int b, int c), int x) { return x; }",
            (2, 2, 1),
        ),
        ("c", "int (*f(int a, int b))(int c) { return 0; }", (2, 2, 2)),
    ],
    ids=[
        "void", "commented-void", "void-pointer", "out-of-line", "pointer-return",
        "reference-return", "variadic", "only-variadic", "callback", "callback-return",
    ],
)
def test_c_declarator_parameter_counts(language: str, source: str, expected: tuple[int, int, int]):
    (fn,) = walk_file(f"a.{language}", language, source.encode()).functions
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == expected


def test_rust_comment_before_receiver():
    fns = _fns("a.rs", "rust", "impl A { fn f(/* receiver */ &self, a: i32) {} }")
    fn = fns["f"]
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == (1, 1, 1)


def test_kotlin_defaults_and_modifiers_are_not_parameters():
    fns = _fns(
        "A.kt", "kotlin",
        'fun f(a: Int = 3, b: String = "x", vararg c: Int) {}\n',
    )
    fn = fns["f"]
    assert (fn.param_count, fn.typed_param_count, fn.primitive_param_count) == (3, 3, 3)


def test_kotlin_constructor_parameter_list():
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    # Constructors are not standalone walker units in Kotlin yet. Exercise
    # the shared signature helpers on the secondary constructor's actual AST.
    source = b'class A {\n    constructor(a: Int, b: String = "x") {}\n}\n'
    tree = Parser(_get_language("kotlin")).parse(source)
    assert not tree.root_node.has_error
    cls = tree.root_node.named_children[0]
    body = next(c for c in cls.named_children if c.type == "class_body")
    ctor = next(c for c in body.named_children if c.type == "secondary_constructor")
    assert _count_parameters(ctor) == 2
    assert typed_param_counts(_parameter_list(ctor), get_language_map("kotlin")) == (2, 2)


@pytest.mark.parametrize(
    "decorators",
    [
        "@click.command()\n@click.option('--a')\n@click.argument('b')\n",
        "@cli.group()\n",
        "@app.command()\n",
        "@click.option('--a')\n",
    ],
)
def test_python_cli_command_signatures_are_fixed(decorators: str):
    fns = _fns("cli.py", "python", f"{decorators}def run({FIVE}):\n    pass\n")
    assert fns["run"].signature_fixed


def test_python_cli_marker_in_a_string_argument_fixes_nothing():
    fns = _fns("a.py", "python", f"@register('command')\ndef run({FIVE}):\n    pass\n")
    assert not fns["run"].signature_fixed


@pytest.mark.parametrize(
    ("signature", "keyword_only"),
    [
        (f"a, *, {FIVE}", 5),
        (f"a, *args: int, {FIVE}, **kw", 6),
        (f"a, *args, {FIVE}", 5),
        (f"{FIVE}, **kw", 0),
        (f"a, /, {FIVE}", 0),
    ],
)
def test_python_keyword_only_parameter_count(signature: str, keyword_only: int):
    fns = _fns("a.py", "python", f"def f({signature}):\n    pass\n")
    assert fns["f"].keyword_only_param_count == keyword_only


def test_python_method_overriding_a_base_in_the_same_file_is_fixed():
    source = (
        "class Base:\n"
        f"    def run(self, {FIVE}):\n        pass\n"
        "class Middle(Base):\n"
        "    pass\n"
        "@dataclass\n"
        "class Child(Middle, metaclass=M):\n"
        "    @cache\n"
        f"    def run(self, {FIVE}):\n        pass\n"
        f"    def walk(self, {FIVE}):\n        pass\n"
        "class Other(Imported):\n"
        f"    def run(self, {FIVE}):\n        pass\n"
    )
    fns = walk_file("a.py", "python", source.encode()).functions
    fixed = {(f.name, f.start_line): f.signature_fixed for f in fns}
    # The base itself, an override reached through Middle, a name Base lacks,
    # and a base imported from elsewhere, which is not followed.
    assert fixed == {
        ("run", 2): False,
        ("run", 9): True,
        ("walk", 11): False,
        ("run", 14): False,
    }


def test_python_class_shadowing_an_imported_base_is_not_its_own_base():
    fns = _fns(
        "a.py",
        "python",
        f"from x import Foo\nclass Foo(Foo):\n    def run(self, {FIVE}):\n        pass\n",
    )
    assert not fns["run"].signature_fixed


def test_python_fastapi_bound_parameters():
    fns = _fns(
        "routes.py",
        "python",
        "async def route(repo_id: str, limit: int = Query(200), q: Annotated[str, Query()] = '',\n"
        "        s: Session = fastapi.Depends(get_db), name: str = 'x', *, h: str = Header(None),\n"
        "        when: str = default_when()):\n    pass\n",
    )
    route = fns["route"]
    assert route.param_count == 7
    assert route.bound_param_count == 4
    # ``h`` is bound, so the keyword-only count leaves it to ``bound_param_count``.
    assert route.keyword_only_param_count == 1
    assert (route.typed_param_count, route.primitive_param_count) == (3, 3)
