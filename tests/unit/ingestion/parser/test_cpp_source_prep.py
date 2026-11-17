"""C++ source sanitizer: macros the grammar misreads and thread-safety annotations."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.lang_helpers.source_prep import _close_paren_of, prepare_cpp_source
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
