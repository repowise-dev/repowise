"""The one shared file-reachability predicate.

Two passes ask "can anything reach this file?": the dead-code analyzer, to
report ``unreachable_file``, and the overview assembler, to drop an execution
flow whose entry point is that file. They used to answer it separately — the
assembler hand-copied the barrel filename set and carried a list of languages
where the analyzer was known to be kinder, because the analyzer's version was
a method reading state built during analysis.

What is asserted here:

- co-change and self-import edges are not importers (the analyzer counted
  both through a raw ``in_degree``; the assembler already excluded them);
- an absent package map means "not checked" and answers reachable, which is
  the one place the predicate is deliberately forgiving;
- a supplied package map gets the real per-language answer;
- both callers reach the same verdict on the same graph.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.dead_code.file_reachability import (
    BARREL_FILENAMES,
    ReachabilityRescues,
    build_package_file_map,
    has_dependency_importer,
    is_file_reachable,
)


def _graph(files: dict[str, dict], edges: list[tuple[str, str, str]] | None = None) -> nx.DiGraph:
    g = nx.DiGraph()
    for path, attrs in files.items():
        g.add_node(path, node_type="file", language=attrs.pop("language", "python"), **attrs)
    for src, dst, edge_type in edges or []:
        g.add_edge(src, dst, edge_type=edge_type)
    return g


def _rescued(graph: nx.DiGraph) -> ReachabilityRescues:
    """Rescue state a caller holding only the graph can build."""
    return ReachabilityRescues(package_files=build_package_file_map(graph))


# ---------------------------------------------------------------------------
# which in-edges count as an importer
# ---------------------------------------------------------------------------


def test_import_edge_makes_a_file_reachable():
    g = _graph(
        {"src/caller.py": {}, "src/service.py": {}},
        [("src/caller.py", "src/service.py", "imports")],
    )
    assert is_file_reachable("src/service.py", g, _rescued(g)) is True


def test_co_change_edge_is_not_an_importer():
    """Committed together is a historical association, not a path code takes."""
    g = _graph(
        {"src/orphan.py": {}, "docs/notes.md": {"language": "markdown"}},
        [("docs/notes.md", "src/orphan.py", "co_changes")],
    )
    assert has_dependency_importer(g, "src/orphan.py") is False
    assert is_file_reachable("src/orphan.py", g, _rescued(g)) is False


def test_self_import_is_not_an_importer():
    """A file importing itself is not evidence that anything else uses it.

    Real shape, not a contrivance: ``celery/contrib/sphinx.py`` and several of
    react's feature-flag forks resolve an import back onto themselves, and a
    raw ``in_degree`` read that as a live importer.
    """
    g = _graph({"src/orphan.py": {}}, [("src/orphan.py", "src/orphan.py", "imports")])
    assert has_dependency_importer(g, "src/orphan.py") is False
    assert is_file_reachable("src/orphan.py", g, _rescued(g)) is False


def test_edge_from_a_symbol_this_file_defines_is_not_an_importer():
    g = _graph({"src/orphan.py": {}})
    g.add_node("src/orphan.py::helper", node_type="symbol", file_path="src/orphan.py")
    g.add_edge("src/orphan.py::helper", "src/orphan.py", edge_type="calls")
    assert is_file_reachable("src/orphan.py", g, _rescued(g)) is False


def test_a_framework_anchor_counts_but_an_external_package_does_not():
    """The split the zombie-package pass already makes. A TYPO3
    ``ext_localconf.php`` has no source-level importer and is not dead."""
    g = _graph({"ext_localconf.php": {"language": "php"}, "vendored.py": {}})
    g.add_node("framework:typo3-core", language="external")
    g.add_node("external:requests", language="external")
    g.add_edge("framework:typo3-core", "ext_localconf.php", edge_type="framework")
    g.add_edge("external:requests", "vendored.py", edge_type="imports")

    assert is_file_reachable("ext_localconf.php", g, _rescued(g)) is True
    assert is_file_reachable("vendored.py", g, _rescued(g)) is False


def test_framework_edge_still_counts():
    """Only association edges are excluded, not every non-import edge type."""
    g = _graph(
        {"src/route.py": {}, "src/app.py": {}},
        [("src/app.py", "src/route.py", "framework")],
    )
    assert is_file_reachable("src/route.py", g, _rescued(g)) is True


# ---------------------------------------------------------------------------
# rescues available to any caller holding the graph
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr", ["is_entry_point", "is_api_contract", "is_never_flag"])
def test_node_flag_rescues_a_file_with_no_importer(attr):
    g = _graph({"src/thing.py": {attr: True}})
    assert is_file_reachable("src/thing.py", g, _rescued(g)) is True


@pytest.mark.parametrize("name", sorted(BARREL_FILENAMES))
def test_barrel_is_reachable(name):
    """A barrel is reached by the names it forwards, never by its own path."""
    path = f"src/feature/{name}"
    g = _graph({path: {}})
    assert is_file_reachable(path, g, _rescued(g)) is True


def test_bundler_alias_target_is_reachable_when_the_caller_supplies_it():
    g = _graph({"src/shims/shiki.ts": {"language": "typescript"}})
    assert is_file_reachable("src/shims/shiki.ts", g, _rescued(g)) is False
    rescues = ReachabilityRescues(
        bundler_alias_targets=frozenset({"src/shims/shiki.ts"}),
        package_files=build_package_file_map(g),
    )
    assert is_file_reachable("src/shims/shiki.ts", g, rescues) is True


def test_a_node_outside_the_graph_is_unchecked_not_unreachable():
    assert is_file_reachable("src/gone.py", _graph({}), None) is True


# ---------------------------------------------------------------------------
# the package-granular languages — the one deliberately forgiving branch
# ---------------------------------------------------------------------------

_PACKAGE_GRANULAR = [
    "internal/scheduler/queue.go",
    "src/main/java/app/Queue.java",
    "src/Queue.kt",
    "src/engine/queue.cpp",
    "include/engine/queue.h",
]


@pytest.mark.parametrize("path", _PACKAGE_GRANULAR)
def test_no_package_map_means_not_checked_and_answers_reachable(path):
    """Their imports name a package, so most files in a live package carry no
    file-level in-edge. A caller without the map would call a whole language
    unreachable, so an unchecked package-granular file is kept."""
    g = _graph({path: {}})
    assert is_file_reachable(path, g, ReachabilityRescues()) is True


@pytest.mark.parametrize("path", _PACKAGE_GRANULAR)
def test_a_supplied_package_map_gets_the_real_answer(path):
    """With the map the predicate stops guessing: a lone file in its own
    package, with no importer and no entry-point sibling, is unreachable."""
    g = _graph({path: {}})
    assert is_file_reachable(path, g, _rescued(g)) is False


@pytest.mark.parametrize(
    ("path", "package_granular"),
    [
        ("src/Queue.GO", False),  # the old assembler regex was case-insensitive
        ("src/Queue.CPP", False),
        ("src/queue.h++", True),  # is_cpp_path covers these; the regex did not
        ("src/queue.inc", True),
    ],
)
def test_which_extensions_count_as_package_granular(path, package_granular):
    """Pinned because the two callers disagreed here and the merge picked one.

    The analyzer's rule wins: ``endswith`` plus ``is_cpp_path``. Both are
    case-sensitive, with one exception that is not an oversight —
    ``_CPP_SOURCE_EXTS`` lists ``.C``, the traditional Unix spelling of a C++
    source. The assembler previously used a case-insensitive regex, so
    an uppercase ``.CPP`` is now checked rather than blanket-rescued, and
    ``.h++`` / ``.inc`` are blanket-rescued where the regex missed them. No
    file in the 41-repo corpus has either spelling, so this pins intent rather
    than recording an observed change.
    """
    g = _graph({path: {}})
    assert is_file_reachable(path, g, ReachabilityRescues()) is package_granular


def test_package_sibling_rescue_applies_through_the_predicate():
    """The per-language helpers are not bypassed — an imported sibling in the
    same package rescues the whole package."""
    g = _graph(
        {
            "internal/q/queue.go": {"language": "go"},
            "internal/q/worker.go": {"language": "go"},
            "cmd/app/main.go": {"language": "go"},
        },
        [("cmd/app/main.go", "internal/q/worker.go", "imports")],
    )
    assert is_file_reachable("internal/q/queue.go", g, _rescued(g)) is True


# ---------------------------------------------------------------------------
# the two callers agree — the whole point of the extraction
# ---------------------------------------------------------------------------


def test_analyzer_flags_a_file_whose_only_in_edge_is_co_change():
    """End to end through the detector, not just the predicate: the analyzer
    read a raw ``in_degree``, so a doc committed alongside a file counted as
    that file's importer and silenced the finding."""
    from repowise.core.analysis.dead_code import DeadCodeAnalyzer
    from repowise.core.analysis.dead_code.constants import _DEFAULT_DYNAMIC_PATTERNS

    g = _graph(
        {"src/orphan.py": {}, "docs/notes.md": {"language": "markdown"}},
        [("docs/notes.md", "src/orphan.py", "co_changes")],
    )
    findings = DeadCodeAnalyzer(g)._detect_unreachable_files(_DEFAULT_DYNAMIC_PATTERNS, set())
    assert "src/orphan.py" in {f.file_path for f in findings}


def test_analyzer_flags_a_file_that_only_imports_itself():
    from repowise.core.analysis.dead_code import DeadCodeAnalyzer
    from repowise.core.analysis.dead_code.constants import _DEFAULT_DYNAMIC_PATTERNS

    g = _graph({"src/orphan.py": {}}, [("src/orphan.py", "src/orphan.py", "imports")])
    findings = DeadCodeAnalyzer(g)._detect_unreachable_files(_DEFAULT_DYNAMIC_PATTERNS, set())
    assert "src/orphan.py" in {f.file_path for f in findings}


def test_the_predicate_is_the_only_thing_deciding_reachability():
    """Which side of the split each rescue sits on, pinned.

    Asserting ``flagged == {n for n in nodes if not is_file_reachable(n)}``
    would look like a strong agreement check and be nearly vacuous, since the
    detector calls that same function. So does ``not flagged - unreachable``
    below: the predicate is the last gate before a finding is made, so that
    containment holds by construction and only guards against reordering.

    The assertion that earns its place is ``unreachable - flagged``, which
    names every file the predicate calls unreachable that the analyzer still
    declines to report. Move the barrel rescue out of the predicate and
    ``__init__.py`` joins that set; move ``is_test`` into the predicate and the
    test file leaves it. Either way this fails.

    What that set means, precisely: the analyzer's remaining skips are
    candidacy rules, "is this a file we would ever report", and the assembler
    deliberately wants none of them applied to an execution flow. Note the
    split is not clean the other way — ``is_entry_point``, ``is_never_flag``
    and the ``__init__.py`` barrel are checked in *both* places, harmlessly,
    since every one of them is idempotent. And ``_should_never_flag`` is a
    reachability rule rather than a candidacy one (``*.sh`` is exempt because
    CI invokes it by name), which is exactly why ``ReachabilityRescues``
    records its glob set as a rescue the second caller is missing.

    Coverage ceiling: the fixture exercises the non-code-language,
    ``is_entry_point`` and ``is_test`` skips. ``_is_synthetic_node``,
    ``_is_fixture_path`` and the never-flag globs are not represented here.
    """
    from repowise.core.analysis.dead_code import DeadCodeAnalyzer
    from repowise.core.analysis.dead_code.constants import _DEFAULT_DYNAMIC_PATTERNS

    g = _graph(
        {
            "src/caller.py": {},
            "src/service.py": {},
            "src/orphan.py": {},
            "src/main.py": {"is_entry_point": True},
            "src/pkg/__init__.py": {},
            "tests/test_orphan.py": {"is_test": True},
            "docs/notes.md": {"language": "markdown"},
        },
        [
            ("src/caller.py", "src/service.py", "imports"),
            ("docs/notes.md", "src/orphan.py", "co_changes"),
        ],
    )

    analyzer = DeadCodeAnalyzer(g)
    rescues = analyzer._reachability_rescues()
    flagged = {
        f.file_path for f in analyzer._detect_unreachable_files(_DEFAULT_DYNAMIC_PATTERNS, set())
    }
    unreachable = {n for n in g.nodes() if not is_file_reachable(str(n), g, rescues)}

    assert flagged == {"src/caller.py", "src/orphan.py"}
    assert "src/service.py" not in flagged
    assert "src/main.py" not in flagged
    assert "src/pkg/__init__.py" not in flagged

    # The gap between the two is candidacy and nothing else. A markdown file
    # and a test file are both unreachable and neither is dead code, and the
    # rules that say so are the analyzer's non-code-language and is_test skips
    # — deliberately not in the predicate, which the assembler also does not
    # want applied to an execution flow.
    assert unreachable - flagged == {"docs/notes.md", "tests/test_orphan.py"}
    assert not flagged - unreachable
