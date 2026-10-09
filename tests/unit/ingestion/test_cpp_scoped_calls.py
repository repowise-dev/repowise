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