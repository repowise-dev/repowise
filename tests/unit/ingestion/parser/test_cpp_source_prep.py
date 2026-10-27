"""C++ source sanitizer: class-head export macros and thread-safety annotations."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.lang_helpers.source_prep import prepare_cpp_source
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
