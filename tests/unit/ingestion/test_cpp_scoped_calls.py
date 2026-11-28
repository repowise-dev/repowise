"""A C++ ``Qualifier::name()`` call must resolve against the qualifier.

``cpp.scm``'s scoped-call pattern captured only ``name: (identifier)``, so the
qualifier written at the call site was discarded and the resolver matched the
leaf name alone. Measured on leveldb before the fix: ``DB::Open(...)`` bound to
a test class's ``RecoveryTest::Open`` and ``Status::Corruption(...)`` to
``CorruptionReporter::Corruption`` -- 52 sites where the source names a class
and we answered with a different one, plus 104 on aria2.

The qualifier is read from source, so this resolves without inferring anything.
Where it cannot help it must DECLINE rather than refuse: a qualifier may name a
namespace, and C++ namespaces are recorded on no symbol.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, caller_file: str) -> set[str]:
    return {
        t
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and str(s).startswith(caller_file + "::")
    }


def _decoy_repo(root: Path) -> None:
    """Two classes declare ``Open``; only one is named at the call site."""
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "test").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "db.h").write_text(
        "#pragma once\nclass DB {\n public:\n  static int Open(int flags);\n};\n"
    )
    (root / "lib" / "db.cc").write_text(
        '#include "lib/db.h"\nint DB::Open(int flags) { return flags; }\n'
    )
    # the decoy: same method name, different class, in a file the caller does
    # not name at the call site
    (root / "test" / "harness.cc").write_text(
        "class RecoveryTest {\n public:\n  int Open(int f) { return f + 1; }\n};\n"
    )
    (root / "lib" / "caller.cc").write_text(
        '#include "lib/db.h"\nint Run() { return DB::Open(2); }\n'
    )


class TestScopedCallUsesTheQualifier:
    def test_resolves_to_the_class_named_at_the_call_site(self, tmp_path: Path) -> None:
        _decoy_repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert any(t.endswith("DB::Open") for t in targets)

    def test_does_not_bind_to_a_same_named_method_on_another_class(
        self, tmp_path: Path
    ) -> None:
        _decoy_repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert not any("RecoveryTest" in t for t in targets)

    def test_declaration_and_its_definition_are_not_read_as_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """The header declares ``DB::Open`` and the .cc defines it.

        A translation unit sees both, and counting them as two candidates made
        every paired method decline.
        """
        _decoy_repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert any(t.endswith("DB::Open") for t in targets)

    def test_qualifier_naming_no_known_class_still_resolves_by_other_tiers(
        self, tmp_path: Path
    ) -> None:
        """A namespace qualifier must DECLINE, not refuse.

        C++ namespaces are recorded on no symbol, so `util::Helper()` cannot be
        matched on its qualifier; the remaining tiers must still see it.
        """
        (tmp_path / "util.h").write_text(
            "#pragma once\nnamespace util {\nint Helper(int v);\n}\n"
        )
        (tmp_path / "util.cc").write_text(
            '#include "util.h"\nnamespace util {\nint Helper(int v) { return v; }\n}\n'
        )
        (tmp_path / "main.cc").write_text(
            '#include "util.h"\nint main() { return util::Helper(1); }\n'
        )
        targets = _call_targets(_build(tmp_path), "main.cc")
        assert any(t.endswith("Helper") for t in targets)

class TestThreePartQualifiedCallProducesACallSite:
    """A three-part qualified call like ``ns::util::toHex()`` produced no
    call site at all, not merely an unresolved one (#1918).

    ``cpp.scm``'s two scoped-call patterns require ``name: (identifier)``.
    tree-sitter nests a three-part qualifier left-recursively, so the outer
    node's ``name`` field is itself a ``qualified_identifier`` and neither
    pattern matches. The call never reaches the resolver, so it cannot be
    resolved, declined, or counted -- it simply is never produced as a
    ``CallSite``.
    """

    def _repo(self, root: Path) -> None:
        (root / "util.h").write_text(
            "#pragma once\nnamespace ns { namespace util {\nint toHex(int x);\n}}\n"
        )
        (root / "util.cc").write_text(
            '#include "util.h"\nnamespace ns { namespace util {\n'
            "int toHex(int x) { return x; }\n}}\n"
        )
        (root / "main.cc").write_text(
            '#include "util.h"\n'
            "int callTwoPart() { return util::toHex(1); }\n"
            "int callThreePart() { return ns::util::toHex(2); }\n"
        )

    def test_three_part_call_resolves_to_the_function(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        graph = _build(tmp_path)
        assert any(
            s.endswith("callThreePart") and t.endswith("toHex")
            for s, t, d in graph.edges(data=True)
            if d.get("edge_type") == "calls"
        )

    def test_two_part_control_is_unaffected(self, tmp_path: Path) -> None:
        """The three-part pattern must not crowd out the existing two-part
        match -- each caller keeps its own distinct edge to toHex."""
        self._repo(tmp_path)
        graph = _build(tmp_path)
        assert any(
            s.endswith("callTwoPart") and t.endswith("toHex")
            for s, t, d in graph.edges(data=True)
            if d.get("edge_type") == "calls"
        )

    def test_unqualified_control_is_unaffected(self, tmp_path: Path) -> None:
        (tmp_path / "lib.h").write_text("#pragma once\nint free_thing(int x);\n")
        (tmp_path / "lib.cc").write_text(
            '#include "lib.h"\nint free_thing(int x) { return x; }\n'
        )
        (tmp_path / "caller.cc").write_text(
            '#include "lib.h"\nint main() { return free_thing(1); }\n'
        )
        targets = _call_targets(_build(tmp_path), "caller.cc")
        assert any(t.endswith("free_thing") for t in targets)


def _edges(graph) -> set[tuple[str, str]]:
    return {(s, t) for s, t, d in graph.edges(data=True) if d.get("edge_type") == "calls"}


class TestNamespaceQualifierNeverNamesAMember:
    """``detail::compare(..)`` names a namespace's function, not a same-named member."""

    def _repo(self, root: Path) -> None:
        (root / "base.h").write_text(
            "#pragma once\n"
            "namespace detail {\n"
            "int compare(const char* a, const char* b, int n) { return 0; }\n"
            "}\n"
            "class string_view {\n"
            " public:\n"
            "  bool starts_with(string_view sv) const {\n"
            "    return detail::compare(data_, sv.data_, 3) == 0;\n"
            "  }\n"
            "  int compare(string_view other) const {\n"
            "    return detail::compare(data_, other.data_, 3);\n"
            "  }\n"
            "  const char* data_;\n"
            "};\n"
        )

    def test_a_member_caller_reaches_the_namespace_function(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        edges = _edges(_build(tmp_path))
        assert ("base.h::string_view::starts_with", "base.h::compare") in edges
        assert ("base.h::string_view::starts_with", "base.h::string_view::compare") not in edges

    def test_a_same_named_member_caller_is_not_recursion(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        edges = _edges(_build(tmp_path))
        assert ("base.h::string_view::compare", "base.h::compare") in edges

    def test_a_friend_defined_in_a_class_is_not_qualified_lookup(self, tmp_path: Path) -> None:
        """Only argument lookup finds a friend its class body defines."""
        (tmp_path / "big.h").write_text(
            "#pragma once\n"
            "class bigint {\n"
            " public:\n"
            "  friend int compare(const bigint& a, const bigint& b) { return 0; }\n"
            "};\n"
        )
        (tmp_path / "user.cc").write_text(
            '#include "big.h"\nint run(const char* a) { return other::compare(a, a, 1); }\n'
        )
        assert not _call_targets(_build(tmp_path), "user.cc")

    def test_a_library_namespace_reaches_no_repository_member(self, tmp_path: Path) -> None:
        (tmp_path / "buf.h").write_text(
            "#pragma once\nstruct counter {\n  int count(int x) const { return x; }\n};\n"
        )
        (tmp_path / "user.cc").write_text(
            '#include "buf.h"\nint run(int x) { return lib::count(x); }\n'
        )
        assert not _call_targets(_build(tmp_path), "user.cc")

    def test_a_namespace_function_wins_over_a_same_named_outer_one(self, tmp_path: Path) -> None:
        """``detail::vformat_to(..)`` means the namespace's, not the caller file's outer one."""
        (tmp_path / "base.h").write_text(
            "#pragma once\nnamespace detail {\nvoid vformat_to(int* buf, int n) {}\n}\n"
        )
        (tmp_path / "format.h").write_text(
            '#pragma once\n#include "base.h"\n'
            "int vformat(int n) {\n"
            "  int buf[4];\n"
            "  detail::vformat_to(buf, n);\n"
            "  return 0;\n"
            "}\n"
            "template <typename It> It vformat_to(It out, int n) { return out; }\n"
        )
        assert _call_targets(_build(tmp_path), "format.h") == {"base.h::vformat_to"}


class TestVariadicOverloadTakesTheCall:
    """A ``va_list`` parameter takes only a ``va_list``: ``Error("%s", s)`` is the variadic one."""

    def _repo(self, root: Path) -> None:
        (root / "util.h").write_text(
            "#pragma once\n#include <stdarg.h>\n"
            "void Error(const char* msg, ...);\n"
            "void Error(const char* msg, va_list ap);\n"
        )
        (root / "util.cc").write_text(
            '#include "util.h"\n'
            "void Error(const char* msg, va_list ap) {}\n"
            "void Error(const char* msg, ...) {\n"
            "  va_list ap;\n"
            "  va_start(ap, msg);\n"
            "  Error(msg, ap);\n"
            "  va_end(ap);\n"
            "}\n"
            "void Report(const char* msg, va_list args) { Error(msg, args); }\n"
            "void Copy(const char* msg) {\n"
            "  va_list first, copy;\n"
            "  Error(msg, copy);\n"
            "}\n"
        )
        (root / "main.cc").write_text(
            '#include "util.h"\nvoid Run(const char* err) {\n  Error("%s", err);\n}\n'
        )

    def test_a_format_argument_calls_the_variadic_overload(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        assert _call_targets(_build(tmp_path), "main.cc") == {"util.cc::Error#2(char*,[])"}

    def test_a_va_list_local_calls_the_va_list_overload(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        edges = _edges(_build(tmp_path))
        assert ("util.cc::Error#2(char*,[])", "util.cc::Error#2(char*,va_list)") in edges

    def test_a_va_list_parameter_calls_the_va_list_overload(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        edges = _edges(_build(tmp_path))
        assert ("util.cc::Report", "util.cc::Error#2(char*,va_list)") in edges

    def test_a_later_va_list_declarator_calls_the_va_list_overload(self, tmp_path: Path) -> None:
        self._repo(tmp_path)
        edges = _edges(_build(tmp_path))
        assert ("util.cc::Copy", "util.cc::Error#2(char*,va_list)") in edges

    def test_a_va_list_of_unknown_type_keeps_the_va_list_overload(self, tmp_path: Path) -> None:
        """A field, a dereference or a global may hold a ``va_list``: nothing refuses it."""
        self._repo(tmp_path)
        (tmp_path / "fwd.cc").write_text(
            '#include "util.h"\n'
            "struct Ctx { va_list ap; };\n"
            "static va_list g_ap;\n"
            'void ByField(Ctx* ctx) { Error("%s", ctx->ap); }\n'
            'void ByPointer(va_list* pap) { Error("%s", *pap); }\n'
            'void ByGlobal() { Error("%s", g_ap); }\n'
        )
        edges = _edges(_build(tmp_path))
        for caller in ("ByField", "ByPointer", "ByGlobal"):
            assert (f"fwd.cc::{caller}", "util.cc::Error#2(char*,va_list)") in edges
