"""C++ source sanitizer: macros the grammar misreads and thread-safety annotations."""

from __future__ import annotations

import time

import pytest

from repowise.core.ingestion.lang_helpers.source_prep import (
    _close_paren_of,
    cpp_class_head_macros,
    prepare_cpp_source,
)
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

EXPORTED_CLASS = b"""namespace leveldb {
class LEVELDB_EXPORT DB {
 public:
  static Status Open(const Options& options, DB** dbptr);
  DB() = default;
  virtual ~DB();
};
}  // namespace leveldb
"""

ANNOTATED_CLASS = b"""class DBImpl {
 public:
  void CompactMemTable() EXCLUSIVE_LOCKS_REQUIRED(mutex_);
  Status Recover(VersionEdit* edit)
      EXCLUSIVE_LOCKS_REQUIRED(mutex_);
 private:
  port::Mutex mutex_;
  MemTable* imm_ GUARDED_BY(mutex_);
  bool has_imm_ GUARDED_BY(mutex_) = false;
};
"""

MACRO_CONSTRUCTOR = b"""struct size_padding {
  unsigned size;

  FMT_CONSTEXPR size_padding(int num_digits, unsigned prefix)
      : size(prefix + num_digits) {
    if (size > 0) size = 1;
  }
};

template <typename T> auto write_int(T out, int value) -> T {
  return out;
}

class buffer {
 public:
  FMT_CONSTEXPR20 buffer(buffer&& other) noexcept : size_(other.size_) {}
  auto size() const -> int { return size_; }

 private:
  int size_;
};
"""

MACRO_LINES = b"""FMT_BEGIN_NAMESPACE
FMT_PRAGMA_CLANG(diagnostic push)

FMT_EXPORT
template <typename Char> class basic_string_view {
 public:
  auto size() const -> int { return size_; }

 private:
  int size_;
};

FMT_EXPORT
enum class align { none, left };

namespace detail {
template <typename T> auto to_unsigned(T value) -> unsigned { return value; }
}  // namespace detail
FMT_END_NAMESPACE
"""


def _symbols(parser: ASTParser, source: bytes) -> set[tuple[str | None, str, str]]:
    result = parser.parse_file(_make_file_info("cpp_pkg/db.h", "cpp"), source)
    return {(s.parent_name, s.name, s.kind) for s in result.symbols}


class TestCppSourcePrep:
    def test_export_macro_class_keeps_its_name_and_first_method(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, EXPORTED_CLASS)
        assert (None, "DB", "class") in symbols
        assert ("DB", "Open", "method") in symbols
        assert not any(name == "LEVELDB_EXPORT" for _, name, _ in symbols)

    def test_annotated_methods_keep_their_names(self, parser: ASTParser) -> None:
        names = {name for _, name, _ in _symbols(parser, ANNOTATED_CLASS)}
        assert {"CompactMemTable", "Recover"} <= names
        assert "EXCLUSIVE_LOCKS_REQUIRED" not in names
        assert "GUARDED_BY" not in names

    def test_macro_constructor_with_initializers_closes_its_class(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, MACRO_CONSTRUCTOR)
        assert (None, "write_int", "function") in symbols
        assert ("buffer", "size", "method") in symbols

    def test_macro_lines_do_not_type_the_next_declaration(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, MACRO_LINES)
        assert ("basic_string_view", "size", "method") in symbols
        assert any(name == "to_unsigned" for _, name, _ in symbols)
        assert not any(name.startswith("FMT_") for _, name, _ in symbols)

    @pytest.mark.parametrize(
        ("source", "macro"),
        [
            (b"class FOO_API Bar {", b"FOO_API "),
            (b"struct A B Bar final : X {", b"A B "),
            (b'class CAPABILITY("mutex") Mutex {', b'CAPABILITY("mutex") '),
            (b"class GTEST_API_ M<std::string>\n    : public B {", b"GTEST_API_ "),
            (b"  int x_ GUARDED_BY(mu_);", b"GUARDED_BY(mu_)"),
            (b"  void F() const REQUIRES(a, f(b));", b"REQUIRES(a, f(b))"),
            (b"  void F() NO_THREAD_SAFETY_ANALYSIS {", b"NO_THREAD_SAFETY_ANALYSIS"),
            (b"  Foo* p ABSL_PT_GUARDED_BY(mu) = nullptr;", b"ABSL_PT_GUARDED_BY(mu)"),
            (b"  FMT_CONSTEXPR Pad(int n) : n_(n) {}", b"FMT_CONSTEXPR "),
            (b"  constexpr FMT_INLINE Args(const S& s)\n      : d_(s) {}", b"FMT_INLINE "),
            (b"  A_API B_INLINE explicit Buf(Buf&& o) noexcept\n  : base(o) {", b"A_API B_INLINE "),
            (b"FMT_BEGIN_NAMESPACE\nnamespace detail {", b"FMT_BEGIN_NAMESPACE"),
            (b"  Q_OBJECT\n public:", b"Q_OBJECT"),
            (b"FMT_EXPORT\r\ntemplate <typename T> struct F;", b"FMT_EXPORT"),
            (b"FMT_EXPORT template <typename T> class S {", b"FMT_EXPORT "),
            (
                b'FMT_PRAGMA_CLANG(diagnostic error "-Wx")\n\nenum class E {};',
                b'FMT_PRAGMA_CLANG(diagnostic error "-Wx")',
            ),
        ],
    )
    def test_blanks_macro_spans_in_place(self, source: bytes, macro: bytes) -> None:
        assert prepare_cpp_source(source) == source.replace(macro, b" " * len(macro))

    @pytest.mark.parametrize(
        "source",
        [
            b"class DB {",
            b"class DB final {",
            b"class DB : public Base {",
            b"struct [[nodiscard]] Foo {",
            b"class alignas(8) Foo {",
            b"class LEVELDB_EXPORT Cache;",
            b"std::vector<A::B> x;",
            b"  if (p) RELEASE(p);",
            b"  return RELEASE(p);",
            b"  RELEASE(p);",
            b"#define GUARDED_BY(x) __attribute__((guarded_by(x)))",
            b"#ifndef NO_THREAD_SAFETY_ANALYSIS",
            b"  int x_ MY_GUARD(mu_);",
            b"  struct TM tm{0};",
            b"  int NO_THREAD_SAFETY_ANALYSIS_X;",
            b"#define F(x) RELEASE(x)",
            # A macro-typed declaration without an initializer list stays.
            b"  FMT_CONSTEXPR Pad(int n) {}",
            b"  HANDLE h(open(path));",
            b"  FOO_T x(1); y = c ? a : b;",
            b"  MACRO_T Make(int n) const;",
            b"  Base::Base(int n) : n_(n) {}",
            b"  Q_INVOKABLE T f(int n) :: x;",
            # A macro line that opens a body, ends the file, continues an
            # expression, or sits in a statement is left alone.
            b"TEST(Suite, Name)\n{",
            b"  Py_BEGIN_ALLOW_THREADS\n  {",
            b"NAPI_MODULE(addon, Init)\n}",
            b"NAPI_MODULE(addon, Init)\n",
            b"  LOG_STATE(x)\n  run();",
            b"REGISTER_OP(Foo)\n    .Input(x);",
            b"#define A \\\n  B_MACRO \\\n  c",
            b"  int x = FLAG_A |\n    FLAG_B;",
            b"enum E {\n  RED,\n  BLUE\n};",
            b"BOOL\nWINAPI\nDllMain(HINSTANCE h)\n{",
            b"HRESULT\r\nFoo::Open(int x) {",
            b"NAPI_MODULE(addon, Init)\nstatic int later;",
            b"#undef A\\\nPI\nstruct API Fwd;",
            b'#define POP() _Pragma("pop_macro(\\"API\\")")\nPOP()\nstruct API Fwd;',
            # A ``)`` with no ``(`` in reach is not a declarator.
            b"  x) GUARDED_BY(mu_);",
            b"  void F(" + b"a," * 2100 + b"b) REQUIRES(mu);",
        ],
    )
    def test_leaves_real_code_alone(self, source: bytes) -> None:
        assert prepare_cpp_source(source) is source

    def test_offsets_and_lines_are_unchanged(self) -> None:
        for source in (EXPORTED_CLASS, ANNOTATED_CLASS):
            out = prepare_cpp_source(source)
            assert len(out) == len(source)
            assert out.splitlines() != source.splitlines()
            assert out.count(b"\n") == source.count(b"\n")


class TestCloseParenOf:
    def test_returns_the_matching_close(self) -> None:
        src = b"Pad(int n) : n_(n) {}"
        assert _close_paren_of(src, src.index(b"(")) == src.index(b")")

    def test_nested_parens_return_the_outer_close(self) -> None:
        src = b"Pad(f(g(x))) : n_(1) {}"
        assert _close_paren_of(src, src.index(b"(")) == src.index(b"))) ") + 2

    def test_unterminated_paren_returns_minus_one(self) -> None:
        src = b"FMT_CONSTEXPR Pad(int n"
        assert _close_paren_of(src, src.index(b"(")) == -1

    def test_close_beyond_the_scan_limit_returns_minus_one(self) -> None:
        src = b"Pad(" + b"a," * 2100 + b"b)"
        assert _close_paren_of(src, 3) == -1

    def test_unterminated_ctor_macro_run_blanks_nothing(self) -> None:
        src = b"FMT_CONSTEXPR Pad(int n"
        assert prepare_cpp_source(src) is src


STACKED_HEAD = b"""class [[nodiscard]] alignas(VECTOR_ALIGNMENT) Vec3
{
public:
\tOVERRIDE_NEW_DELETE

\tVec3() = default;
\tfloat Length() const;
};

struct [[nodiscard]] alignas(max(A_ALIGN, B_ALIGN)) DMat {
\tvoid Inverse();
};
"""

# A class head split by an ``#ifndef`` group, with a comment on the directive.
SPLIT_HEAD = b"""class
#ifndef DOC_TOOL // the doc tool chokes here
\tLIB_EXPORT_WORKAROUND alignas(max(A_ALIGN, B_ALIGN))
#endif
\tBody : public NonCopyable
{
public:
\tbool IsRigidBody() const { return true; }
};
"""

SPLIT_MEMBERS = b"""class Constraint : public Base
{
public:
\texplicit Constraint(const Settings &inSettings) :
#ifdef DEBUG_RENDERER
\t\tmDrawSize(inSettings.mDrawSize),
#endif
\t\tmPriority(inSettings.mPriority)
\t{
\t}

private:
#ifdef COMPILER_CLANG
\t__attribute__((no_sanitize("implicit-conversion"))) // overflow is intended
#endif
\tinline int NextSequence(int inIndex) { return ++mSeq[inIndex]; }
\tvoid Validate() const;
\tint mDrawSize, mPriority, mSeq[4];
};
"""

FIRST_MEMBER_MACRO = b"""class LIB_EXPORT Settings : public RefTarget<Settings>
{
\tDECLARE_SERIALIZABLE_NON_VIRTUAL(LIB_EXPORT, Settings)

public:
\tstruct LIB_EXPORT Weight : public CWeight
\t{
\t\tDECLARE_SERIALIZABLE_NON_VIRTUAL(LIB_EXPORT, Weight)
\t};

\tvoid Init();
};
"""

# Reflection macros with a string or grouped argument, with and without ``;``.
MEMBER_MACROS = b"""namespace demo
{
    class Widget final
        : public Base
    {
    public:

        TYPE_RTTI(Widget, "{6D92B9BE-C5E4-4571-B0FA-8F29042BE93B}", Base);
        TYPE_NAME(Widget, "Renderer \"main\"");
        CLASS_ALLOCATOR(Widget, SystemAllocator);

        Widget(int a, int b);
        void Draw();
    };

    template <class T>
    class Box : public Widget
    {
        TYPE_RTTI((Box, "{EE8293C3-9B1E-470B-9922-2CBB8DA13D78}", T), Widget)
        TYPE_RTTI_MULTI(
            Box, "{11111111-2222-3333-4444-555555555555}")
    public:
        void Put(T t);
    };

    void Report()
    {
        LOG_INFO("Report", "{not a member}");
    }
}
"""


class TestClassBodyRecovery:
    def test_stacked_head_specifiers_keep_the_class(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, STACKED_HEAD)
        assert {(None, "Vec3", "class"), ("Vec3", "Length", "method")} <= symbols
        assert {(None, "DMat", "struct"), ("DMat", "Inverse", "method")} <= symbols

    def test_minimal_stacked_head(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, b"class [[nodiscard]] alignas(16) Foo { void f(); };\n")
        assert symbols == {(None, "Foo", "class"), ("Foo", "f", "method")}

    def test_directive_inside_a_class_head(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, SPLIT_HEAD)
        assert {(None, "Body", "class"), ("Body", "IsRigidBody", "method")} <= symbols
        assert not any(name == "NonCopyable" for _, name, _ in symbols)

    def test_directive_inside_an_initializer_list_and_before_a_member(
        self, parser: ASTParser
    ) -> None:
        symbols = _symbols(parser, SPLIT_MEMBERS)
        assert ("Constraint", "Constraint", "method") in symbols
        assert ("Constraint", "NextSequence", "method") in symbols
        assert ("Constraint", "Validate", "method") in symbols

    def test_call_shaped_macro_on_the_first_member_line(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, FIRST_MEMBER_MACRO)
        assert {(None, "Settings", "class"), ("Settings", "Init", "method")} <= symbols
        assert ("Settings", "Weight", "struct") in symbols or (None, "Weight", "struct") in symbols

    def test_member_macro_with_a_string_argument_keeps_the_class(self, parser: ASTParser) -> None:
        symbols = _symbols(parser, MEMBER_MACROS)
        assert {(None, "Widget", "class"), ("Widget", "Draw", "method")} <= symbols
        assert {(None, "Box", "class"), ("Box", "Put", "method")} <= symbols
        out = prepare_cpp_source(MEMBER_MACROS)
        assert b"TYPE_RTTI" not in out and b"TYPE_NAME" not in out
        assert b'LOG_INFO("Report", "{not a member}");' in out

    @pytest.mark.parametrize(
        "source", [STACKED_HEAD, SPLIT_HEAD, SPLIT_MEMBERS, FIRST_MEMBER_MACRO, MEMBER_MACROS]
    )
    def test_offsets_and_line_breaks_are_unchanged(self, source: bytes) -> None:
        out = prepare_cpp_source(source)
        assert len(out) == len(source)
        assert [i for i, b in enumerate(out) if b == 0x0A] == [
            i for i, b in enumerate(source) if b == 0x0A
        ]

    def test_only_the_head_macros_are_reported(self) -> None:
        assert cpp_class_head_macros(SPLIT_HEAD) == {"LIB_EXPORT_WORKAROUND"}
        assert cpp_class_head_macros(STACKED_HEAD) == frozenset()

    @pytest.mark.parametrize(
        "source",
        [
            # A group whose arm ends a declaration parses as it always did.
            b"class A {\n#ifdef X\n  void f();\n#else\n  void g();\n#endif\n};\n",
            # A statement-position call in a function body is a real call.
            b"void f() {\n  LOG(x)\n  g();\n}\n",
            b"enum class E {\n  ENTRY(a)\n};\n",
            # Constructors and methods of all-caps classes are no macros.
            b"struct JSON {\n  JSON(int a) noexcept;\n  JSON(int a) noexcept : x(a) {}\n};\n",
            b"struct S {\n  UI(int a) const;\n};\n",
            # An alternative operator after a call is an expression, not a prefix.
            b"void f(int x) {\n  CHECK(x) and run();\n}\n",
            b"struct RGB {\n  RGB(int r)\n    : r_(r) {}\n  int r_;\n};\n",
            b"struct HTTP {\n  HTTP(int c)\n  {\n  }\n};\n",
            b"struct GUID {\n  GUID(int a) { v = a; }\n  int v;\n};\n",
        ],
    )
    def test_whole_constructs_are_left_alone(self, source: bytes) -> None:
        assert prepare_cpp_source(source) is source

    def test_many_class_words_scan_in_linear_time(self) -> None:
        source = b"class [[a]] " * 20000
        start = time.perf_counter()
        prepare_cpp_source(source)
        assert time.perf_counter() - start < 2.0

    def test_commented_class_head_scans_in_linear_time(self) -> None:
        # Doc comments after ``class`` used to let a specifier start inside any word.
        source = (
            b"/// class\n"
            + b"///\t\tADD_BASE_CLASS(Bar, Foo) // DO JUST ADD BASE CLASS EVERY TIME\n" * 40
        )
        start = time.perf_counter()
        prepare_cpp_source(source)
        assert time.perf_counter() - start < 1.0
